"""
TFT 주가 예측 → AI 보고서 생성 FastAPI 서버

흐름:
  1. POST /report  → callback JSON 수신, job 등록 후 202 즉시 반환
  2. BackgroundTask → 프롬프트 조립 → Gemini API 호출 → 보고서 저장
  3. GET  /report/{job_id} → 상태 / 결과 폴링
"""

import asyncio
import os
import uuid
from datetime import datetime
from typing import Optional

import google.generativeai as genai
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

# ── 설정 ─────────────────────────────────────────────────────────────────────
# export GEMINI_API_KEY="AIza..."  (무료 티어: 분당 15회, 일 1500회)
genai.configure(api_key=os.environ["GEMINI_API_KEY"])
GEMINI_MODEL = "gemini-2.0-flash"   # 무료 티어 지원 최신 모델
MAX_TOKENS   = 2048

# ── 간이 Job 저장소 (운영 시 Redis로 교체) ────────────────────────────────────
job_store: dict[str, dict] = {}

app = FastAPI(title="TFT Stock Report API")


# ════════════════════════════════════════════════════════════════════════════════
# Pydantic 스키마 — callback JSON 그대로 매핑
# ════════════════════════════════════════════════════════════════════════════════

class FeatureItem(BaseModel):
    rank:          Optional[int]   = None
    feature:       str
    importance:    float
    current_value: Optional[float] = None

class TopFeatures(BaseModel):
    unknown_past: list[FeatureItem] = []
    known_future: list[FeatureItem] = []

class AttentionPeak(BaseModel):
    minutes_ago: int
    attention:   float

class Interpretability(BaseModel):
    top_features:   TopFeatures
    attention_peak: list[AttentionPeak] = []

class SectorInfo(BaseModel):
    description: str
    importance:  float                  # 0.0 ~ 1.0

class MarketInfo(BaseModel):
    description: str
    importance:  float

class CallbackPayload(BaseModel):
    ticker:           str
    ticker_name:      str
    trade_datetime:   str               # "2026-05-22 10:00:00"
    pred_str:         str               # "매수" | "관망" | "매도"
    prob_buy:         float
    prob_hold:        float
    prob_sell:        float
    sector:           SectorInfo
    market:           MarketInfo
    interpretability: Interpretability

class JobStatus(BaseModel):
    job_id:     str
    status:     str                     # pending | running | done | error
    created_at: str
    result:     Optional[str]  = None   # 완성된 보고서 마크다운
    error:      Optional[str]  = None


# ════════════════════════════════════════════════════════════════════════════════
# 피처 해석 매핑표
# ════════════════════════════════════════════════════════════════════════════════

FEATURE_META: dict[str, dict] = {
    "rsi_14":            {"label": "RSI(14) 모멘텀 지표",           "interp": lambda v: f"{v:.1f} ({'과매도' if v < 30 else '과매수' if v > 70 else '중립'} 구간)"},
    "log_ret":           {"label": "직전 봉 로그 수익률",            "interp": lambda v: f"{v:+.4f} ({'직전 봉 상승' if v > 0 else '직전 봉 하락'})"},
    "disparity_5":       {"label": "5봉 이동평균 이격도",            "interp": lambda v: f"{v:.3f} ({'단기 과열' if v > 1.03 else '단기 침체' if v < 0.97 else '정상 범위'})"},
    "disparity_20":      {"label": "20봉 이동평균 이격도",           "interp": lambda v: f"{v:.3f} ({'중기 과열' if v > 1.05 else '중기 침체' if v < 0.95 else '정상 범위'})"},
    "disparity_60":      {"label": "60봉 이동평균 이격도",           "interp": lambda v: f"{v:.3f} ({'장기 과열' if v > 1.1  else '장기 침체' if v < 0.9  else '정상 범위'})"},
    "bb_position":       {"label": "볼린저밴드 내 위치",             "interp": lambda v: f"{v:.3f} ({'상단 돌파권' if v > 0.8 else '하단 접근' if v < 0.2 else '중간 구간'})"},
    "vol_ratio":         {"label": "거래량 비율 (20봉 평균 대비)",   "interp": lambda v: f"{v:.2f}배 ({'거래량 급증' if v > 2.0 else '거래량 급감' if v < 0.5 else '평이한 거래'})"},
    "macd_ratio":        {"label": "MACD 비율",                      "interp": lambda v: f"{v:+.5f} ({'상승 모멘텀' if v > 0 else '하락 모멘텀'})"},
    "macd_hist_ratio":   {"label": "MACD 히스토그램 비율",           "interp": lambda v: f"{v:+.5f}"},
    "rel_close":         {"label": "당일 시가 대비 현재가 비율",     "interp": lambda v: f"{v:+.4f} ({'시초가 대비 상승' if v > 0 else '시초가 대비 하락'})"},
    "kospi_ret":         {"label": "KOSPI 당일 등락률",              "interp": lambda v: f"{v:+.4f} ({'상승' if v > 0 else '하락'})"},
    "kosdaq_ret":        {"label": "KOSDAQ 당일 등락률",             "interp": lambda v: f"{v:+.4f} ({'상승' if v > 0 else '하락'})"},
    "snp500_ret":        {"label": "전일 S&P500 등락률",             "interp": lambda v: f"{v:+.4f}"},
    "nasdaq_ret":        {"label": "전일 나스닥 등락률",             "interp": lambda v: f"{v:+.4f}"},
    "phlx_semi_ret":     {"label": "전일 필라델피아 반도체 등락률",  "interp": lambda v: f"{v:+.4f}"},
    "vix_chg":           {"label": "VIX 공포지수 변동",              "interp": lambda v: f"{v:+.4f} ({'불확실성 증가' if v > 0 else '안도 심리'})"},
    "usd_krw_chg":       {"label": "원달러 환율 변동",               "interp": lambda v: f"{v:+.2f}원 ({'원화 약세' if v > 0 else '원화 강세'})"},
    "prop_foreign":      {"label": "외국인 순매수 비율",             "interp": lambda v: f"{v:+.4f} ({'외국인 순매수' if v > 0 else '외국인 순매도'})"},
    "prop_individual":   {"label": "개인 순매수 비율",               "interp": lambda v: f"{v:+.4f} ({'개인 순매수' if v > 0 else '개인 순매도'})"},
    "prop_institution":  {"label": "기관 순매수 비율",               "interp": lambda v: f"{v:+.4f} ({'기관 순매수' if v > 0 else '기관 순매도'})"},
    "per":               {"label": "PER (주가수익비율)",             "interp": lambda v: f"{v:.1f}배"},
    "pbr":               {"label": "PBR (주가순자산비율)",           "interp": lambda v: f"{v:.2f}배 ({'청산가치 이하' if v < 1 else '청산가치 이상'})"},
    "sector_ret_1d":     {"label": "소속 섹터 당일 등락률",          "interp": lambda v: f"{v:+.4f}"},
    "sector_volume_ratio":{"label":"섹터 거래량 비율",               "interp": lambda v: f"{v:.2f}배 ({'섹터 이상 거래 발생' if v > 2.0 else '정상 거래량'})"},
    "day_of_week":       {"label": "요일",                           "interp": lambda v: f"{['월','화','수','목','금'][int(v)]}요일"},
    "listing_days":      {"label": "상장 경과일",                    "interp": lambda v: f"{int(v):,}일"},
    # known_future
    "time_progress":     {"label": "장 진행률",                      "interp": lambda v: f"{v*100:.1f}% (09:00 이후 약 {v*390:.0f}분 경과)"},
    "is_bok":            {"label": "한국은행 금통위일",              "interp": lambda v: "금통위 결과 발표일 (변동성 확대 가능)" if v else "해당 없음"},
    "is_fomc":           {"label": "FOMC 영향일",                    "interp": lambda v: "전날 FOMC 결과 반영 중" if v else "해당 없음"},
    "is_witching_kr":    {"label": "한국 선물옵션 만기일",           "interp": lambda v: "프로그램 매물 출회 가능" if v else "해당 없음"},
    "is_witching_us":    {"label": "미국 선물옵션 만기 영향일",      "interp": lambda v: "전날 미국 만기 영향" if v else "해당 없음"},
}

def interpret_feature(feat: FeatureItem) -> str:
    """피처 하나를 '레이블 — 현재값 (해석)' 문자열로 변환."""
    meta = FEATURE_META.get(feat.feature)
    label = meta["label"] if meta else feat.feature

    if feat.current_value is None:
        return f"  • {label} (중요도 {feat.importance*100:.1f}%) — 값 미제공"

    try:
        interp = meta["interp"](feat.current_value) if meta else str(feat.current_value)
    except Exception:
        interp = str(feat.current_value)

    return f"  • {label} — {interp}  [중요도 {feat.importance*100:.1f}%]"


# ════════════════════════════════════════════════════════════════════════════════
# 프롬프트 조립
# ════════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """당신은 한국 주식 시장 AI 예측 분석가입니다.
TFT(Temporal Fusion Transformer) 딥러닝 모델이 생성한 주가 예측 결과와
모델 내부의 피처 중요도·어텐션 데이터를 바탕으로,
투자자가 이해할 수 있는 예측 보고서를 작성합니다.

보고서 작성 원칙:
- 모델이 왜 해당 예측을 내렸는지 근거 중심으로 서술한다.
- 투자 결정을 강요하지 않으며, 분석 근거를 중립적으로 제시한다.
- 기술 용어는 괄호 안에 간단한 설명을 병기한다.
- 수치는 반드시 해석과 함께 제시한다 (숫자만 나열하지 않는다).
- 보고서 말미에는 반드시 투자 유의사항을 포함한다."""


def build_user_prompt(p: CallbackPayload) -> str:
    """callback payload → 사용자 프롬프트 문자열."""

    # ── Unknown Past 피처 텍스트 ──
    past_lines = "\n".join(
        f"{f.rank}위. " + interpret_feature(f)
        for f in sorted(p.interpretability.top_features.unknown_past,
                        key=lambda x: x.rank or 99)
    )

    # ── Known Future 피처 텍스트 ──
    known_lines = []
    all_zero = all(
        (f.current_value or 0) == 0
        for f in p.interpretability.top_features.known_future
    )
    if all_zero:
        known_lines.append("  특별한 이벤트 없음 (금통위 · FOMC · 선물만기 해당 없음)")
    else:
        for f in p.interpretability.top_features.known_future:
            known_lines.append(interpret_feature(f))
    known_text = "\n".join(known_lines)

    # ── 어텐션 피크 텍스트 ──
    attn_parts = [
        f"{a.minutes_ago}분 전 봉 (어텐션 {a.attention*100:.1f}%)"
        for a in sorted(p.interpretability.attention_peak,
                        key=lambda x: x.attention, reverse=True)
    ]
    attention_text = " — ".join(attn_parts) if attn_parts else "데이터 없음"

    # ── 예측 이모지 & 신뢰도 표현 ──
    if p.prob_buy >= 0.8:
        confidence_text = "모델이 높은 확신을 보이고 있습니다."
    elif p.prob_buy >= 0.6 or p.prob_sell >= 0.6:
        confidence_text = "모델이 다소 우세한 방향성을 제시합니다."
    else:
        confidence_text = "방향성이 불명확하여 신중한 접근이 필요합니다."

    return f"""다음 데이터를 바탕으로 {p.ticker_name}({p.ticker}) 예측 보고서를 작성해 주세요.

─────────────────────────────────────────
[예측 기준 시각]
{p.trade_datetime}

[모델 예측 결과]
예측: {p.pred_str}
매수 확률: {p.prob_buy*100:.1f}%
관망 확률: {p.prob_hold*100:.1f}%
매도 확률: {p.prob_sell*100:.1f}%
신뢰도 평가: {confidence_text}

[모델이 주목한 주요 피처 (상위 5개)]
{past_lines}

[이벤트 피처]
{known_text}

[모델이 집중한 시점 (어텐션 상위)]
{attention_text}

[종목 정보]
섹터: {p.sector.description} (중요도: {p.sector.importance*100:.1f}%)
시장: {p.market.description} (중요도: {p.market.importance*100:.1f}%)
─────────────────────────────────────────

아래 형식에 맞춰 보고서를 작성해 주세요.

## {p.ticker_name} ({p.ticker}) AI 예측 보고서
**예측 시각**: {p.trade_datetime}
**예측 방향**: [이모지] [매수/관망/매도]  (확률 표기)

---

### 📊 예측 신뢰도
(확률 표 + 한 줄 평)

---

### 🔍 주요 판단 근거
**[기술적 지표]** / **[수급 흐름]** / **[시장 환경]** 소제목으로 구분하여 서술

---

### ⏱ 모델이 주목한 시점
(어텐션 피크 해석)

---

### 📅 오늘의 이벤트
(이벤트 피처 해석)

---

### 🏭 섹터 및 시장
(섹터·시장 비중 해석)

---

### ⚠️ 투자 유의사항
(고정 문구)"""


# ════════════════════════════════════════════════════════════════════════════════
# 백그라운드 작업
# ════════════════════════════════════════════════════════════════════════════════

async def generate_report_task(job_id: str, payload: CallbackPayload) -> None:
    job_store[job_id]["status"] = "running"
    try:
        user_prompt = build_user_prompt(payload)

        model = genai.GenerativeModel(
            model_name        = GEMINI_MODEL,
            system_instruction = SYSTEM_PROMPT,
        )
        # Gemini Python SDK는 동기 → asyncio.to_thread로 비동기 처리
        response = await asyncio.to_thread(
            model.generate_content,
            user_prompt,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=MAX_TOKENS,
                temperature=0.4,
            ),
        )
        report_text = response.text

        job_store[job_id]["status"] = "done"
        job_store[job_id]["result"] = report_text

    except Exception as e:
        job_store[job_id]["status"] = "error"
        job_store[job_id]["error"]  = f"Gemini API 오류: {e}"



# ════════════════════════════════════════════════════════════════════════════════
# 엔드포인트
# ════════════════════════════════════════════════════════════════════════════════

@app.post("/report", response_model=JobStatus, status_code=202)
async def create_report(payload: CallbackPayload, bg: BackgroundTasks):
    """TFT callback JSON → 비동기 보고서 생성 시작. job_id 즉시 반환."""
    job_id = str(uuid.uuid4())
    job_store[job_id] = {
        "job_id":     job_id,
        "status":     "pending",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "result":     None,
        "error":      None,
    }
    bg.add_task(generate_report_task, job_id, payload)
    return job_store[job_id]


@app.get("/report/{job_id}", response_model=JobStatus)
async def get_report(job_id: str):
    """폴링 엔드포인트 — pending / running / done / error 상태 반환."""
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_id를 찾을 수 없습니다.")
    return job


@app.get("/health")
async def health():
    return {"status": "ok", "total_jobs": len(job_store)}
