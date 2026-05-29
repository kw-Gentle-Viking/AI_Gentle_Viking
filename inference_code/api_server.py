"""
api_server.py
=============
AI 추론 서버 (FastAPI)

현재 네트워크 구성: Option B (폴링)
    - 백엔드→AI 커맨드 전달은 poll_commands.py가 폴링 방식으로 처리
    - 이 파일의 POST /command 엔드포인트는 Option A (공인 IP 직접 수신) 전환 시 사용
    - poll_commands.py가 run_once_inference / load_tickers / save_tickers를 import해서 사용

실행 (Option B에서는 선택적 — /health 모니터링 목적):
    conda activate kis_collector
    uvicorn api_server:app --host 0.0.0.0 --port 8000

환경변수:
    AI_SERVER_API_KEY   : 인증 키 (Option A에서 X-API-Key 헤더 검증용)
    BACKEND_WEBHOOK_URL : 5분 결과 push URL (push_realtime_results.py와 공유)
    TFT_MODEL_PATH      : 모델 가중치 경로 (기본: /home/user/best_model_state_dict.pt)
"""

import os
import json
import logging
import threading
import requests
import numpy as np
import psycopg2
import torch
import torch.nn.functional as F
from datetime import date, datetime, timedelta
from fastapi import FastAPI, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel
from omegaconf import OmegaConf
from tft_torch.tft import TemporalFusionTransformer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("api_server.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================
# 설정 (inference.py와 반드시 동일하게 유지)
# ============================================================
DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     os.environ.get("DB_PORT", 5432),
    "dbname":   os.environ.get("DB_NAME", "stock_db"),
    "user":     os.environ.get("DB_USER", "stock_user"),
    "password": os.environ.get("DB_PASSWORD", "your_password"),
}

MODEL_PATH          = os.environ.get("TFT_MODEL_PATH", "/home/user/best_model_state_dict.pt")
AI_SERVER_API_KEY   = os.environ.get("AI_SERVER_API_KEY", "changeme")
BACKEND_WEBHOOK_URL = os.environ.get("BACKEND_WEBHOOK_URL", "")
TICKERS_FILE        = "/home/user/active_tickers.json"
ENCODER_LENGTH      = 60

# 피처 목록 (inference.py와 동일 — 수정 시 두 파일 함께 수정)
INFERENCE_COLS = [
    "log_ret_1d", "disparity_5d", "disparity_20d", "disparity_60d", "volatility_20d",
    "prop_individual", "prop_foreign", "prop_institution",
    "per", "pbr", "per_chg_1d", "pbr_chg_1d",
    "kospi_ret", "kosdaq_ret", "snp500_ret", "nasdaq_ret", "phlx_semi_ret",
    "vix_chg", "usd_krw_chg", "us_10y_yield_chg", "rate_spread_us_kr", "wti_ret", "gold_ret",
    "sector_ret_1d", "sector_ret_5d", "sector_ret_20d",
    "sector_ma_ratio_20d", "sector_volatility", "sector_volume_ratio",
    "is_dividend", "is_bonus_issue", "is_rights_offering", "is_split", "is_merger", "is_earnings",
    "is_bok", "is_fomc", "is_witching_kr", "is_witching_us",
    "sector_id", "market_id", "day_of_week", "listing_days",
]
REALTIME_COLS = [
    "time_progress",
    "rel_close", "rel_high", "rel_low", "log_ret",
    "disparity_5", "disparity_20", "disparity_60",
    "vol_ratio", "rsi_14", "bb_position",
    "macd_ratio", "macd_signal_ratio", "macd_hist_ratio",
]
KNOWN_FUTURE_COLS = ["time_progress", "is_bok", "is_fomc", "is_witching_kr", "is_witching_us"]
STATIC_COLS       = ["sector_id", "market_id"]
UNKNOWN_PAST_COLS = [c for c in REALTIME_COLS + INFERENCE_COLS
                     if c not in KNOWN_FUTURE_COLS and c not in STATIC_COLS]
HISTORICAL_COLS   = UNKNOWN_PAST_COLS + KNOWN_FUTURE_COLS  # 55개

FEATURE_DESC = {
    "rsi_14": "RSI(14)", "log_ret": "직전 봉 로그 수익률", "disparity_5": "5봉 이동평균 이격도",
    "disparity_20": "20봉 이동평균 이격도", "disparity_60": "60봉 이동평균 이격도",
    "bb_position": "볼린저밴드 위치", "vol_ratio": "거래량 비율", "macd_ratio": "MACD 비율",
    "macd_signal_ratio": "MACD 시그널 비율", "macd_hist_ratio": "MACD 히스토그램 비율",
    "rel_close": "당일 시가 대비 현재가", "rel_high": "당일 시가 대비 고가", "rel_low": "당일 시가 대비 저가",
    "kospi_ret": "KOSPI 당일 등락률", "kosdaq_ret": "KOSDAQ 당일 등락률",
    "snp500_ret": "S&P500 전일 등락률", "nasdaq_ret": "나스닥 전일 등락률",
    "phlx_semi_ret": "필라델피아 반도체 등락률", "vix_chg": "VIX 변동",
    "usd_krw_chg": "원달러 환율 변동", "us_10y_yield_chg": "미국채 10년물 변동",
    "rate_spread_us_kr": "한미 금리차", "wti_ret": "WTI 등락률", "gold_ret": "금 등락률",
    "prop_foreign": "외국인 순매수 비율", "prop_individual": "개인 순매수 비율",
    "prop_institution": "기관 순매수 비율", "per": "PER", "pbr": "PBR",
    "per_chg_1d": "PER 전일 변화율", "pbr_chg_1d": "PBR 전일 변화율",
    "log_ret_1d": "전일 로그 수익률", "disparity_5d": "5일 이동평균 이격도",
    "disparity_20d": "20일 이동평균 이격도", "disparity_60d": "60일 이동평균 이격도",
    "volatility_20d": "20일 변동성",
    "sector_ret_1d": "섹터 1일 등락률", "sector_ret_5d": "섹터 5일 등락률",
    "sector_ret_20d": "섹터 20일 등락률", "sector_ma_ratio_20d": "섹터 20일 이격도",
    "sector_volatility": "섹터 변동성", "sector_volume_ratio": "섹터 거래량 비율",
    "is_dividend": "배당 공시", "is_bonus_issue": "무상증자", "is_rights_offering": "유상증자",
    "is_split": "액면분할", "is_merger": "합병", "is_earnings": "실적발표",
    "day_of_week": "요일", "listing_days": "상장 경과일",
    "time_progress": "장 진행률", "is_bok": "금통위 여부", "is_fomc": "FOMC 여부",
    "is_witching_kr": "한국 선물만기", "is_witching_us": "미국 선물만기",
    "sector_id": "섹터", "market_id": "시장",
}

TFT_CONFIG = OmegaConf.create({
    "task_type": "regression", "target_window_start": None,
    "data_props": {
        "num_historical_numeric": len(HISTORICAL_COLS), "num_historical_categorical": 0,
        "historical_categorical_cardinalities": [],
        "num_static_numeric": 0, "num_static_categorical": 2,
        "static_categorical_cardinalities": [21, 3],
        "num_future_numeric": len(KNOWN_FUTURE_COLS), "num_future_categorical": 0,
        "future_categorical_cardinalities": [],
    },
    "model": {"state_size": 32, "attention_heads": 4, "dropout": 0.1,
              "lstm_layers": 1, "output_quantiles": [0.1, 0.5, 0.9]},
})

LABEL_MAP = {0: "매수", 1: "관망", 2: "매도"}

# ============================================================
# 모델 (프로세스 시작 시 1회 로드)
# ============================================================
_model = None
_model_lock = threading.Lock()

def get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                m = TemporalFusionTransformer(TFT_CONFIG)
                m.load_state_dict(torch.load(MODEL_PATH, map_location="cpu"))
                m.eval()
                _model = m
                logger.info("모델 로드 완료")
    return _model


# ============================================================
# 종목 관리
# ============================================================
_tickers_lock = threading.Lock()

def load_tickers() -> dict:
    if not os.path.exists(TICKERS_FILE):
        return {"updated_at": "", "users": {}, "all_tickers": []}
    with open(TICKERS_FILE) as f:
        return json.load(f)

def save_tickers(data: dict):
    data["updated_at"] = datetime.now().isoformat()
    with _tickers_lock:
        with open(TICKERS_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


# ============================================================
# ONCE 추론 (해석 가능성 포함)
# ============================================================
def get_conn():
    return psycopg2.connect(**DB_CONFIG)

def load_features_for_once(tickers: list):
    today         = date.today()
    lookback_date = today - timedelta(days=5)
    conn = get_conn()
    cur  = conn.cursor()
    placeholders = ",".join(["%s"] * len(tickers))

    cur.execute(f"""
        SELECT MAX(trade_date) FROM inference_features
        WHERE ticker IN ({placeholders})
    """, tickers)
    inf_date = cur.fetchone()[0]
    if inf_date is None:
        cur.close(); conn.close(); return None

    cur.execute(f"""
        SELECT ticker, {', '.join(INFERENCE_COLS)}
        FROM inference_features
        WHERE trade_date = %s AND ticker IN ({placeholders})
    """, [inf_date] + tickers)
    inf_rows = cur.fetchall()

    cur.execute(f"""
        SELECT ticker, trade_datetime, {', '.join(REALTIME_COLS)}
        FROM realtime_features
        WHERE trade_date >= %s AND ticker IN ({placeholders})
        ORDER BY ticker, trade_datetime
    """, [lookback_date] + tickers)
    rt_rows = cur.fetchall()
    cur.close(); conn.close()

    if not inf_rows or not rt_rows:
        return None

    import pandas as pd
    df_inf = pd.DataFrame(inf_rows, columns=["ticker"] + INFERENCE_COLS)
    df_rt  = pd.DataFrame(rt_rows,  columns=["ticker", "trade_datetime"] + REALTIME_COLS)
    df_rt["trade_datetime"] = pd.to_datetime(df_rt["trade_datetime"])
    return df_rt.merge(df_inf, on="ticker", how="left")

def build_batch_for_once(df):
    import pandas as pd
    df = df.sort_values(["ticker", "trade_datetime"]).reset_index(drop=True)
    for col in HISTORICAL_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    hist_list, fut_list, static_list, tickers, last_dts = [], [], [], [], []
    for ticker in sorted(df["ticker"].unique()):
        grp = df[df["ticker"] == ticker].sort_values("trade_datetime").tail(ENCODER_LENGTH + 1)
        if len(grp) < ENCODER_LENGTH + 1:
            continue
        hist = grp.iloc[:ENCODER_LENGTH]
        fut  = grp.iloc[ENCODER_LENGTH:]
        hist_list.append(hist[HISTORICAL_COLS].values.astype(np.float32))
        fut_list.append(fut[KNOWN_FUTURE_COLS].values.astype(np.float32))
        static_list.append([
            min(max(int(grp["sector_id"].fillna(20).iloc[-1]), 0), 20),
            min(max(int(grp["market_id"].fillna(2).iloc[-1]),  0), 2),
        ])
        tickers.append(ticker)
        last_dts.append(grp["trade_datetime"].iloc[-1])
        # 마지막 encoder 봉의 피처 값 보존 (current_value용)
        hist_list[-1]  # already saved

    if not tickers:
        return None, [], [], []

    # current_value 추출: 마지막 encoder 봉 (decoder 직전 봉)
    last_enc_vals = [
        df[(df["ticker"] == t)].sort_values("trade_datetime")
        .tail(ENCODER_LENGTH + 1).iloc[ENCODER_LENGTH - 1][HISTORICAL_COLS].to_dict()
        for t in tickers
    ]

    batch = {
        "historical_ts_numeric":    torch.tensor(np.array(hist_list),   dtype=torch.float32),
        "future_ts_numeric":        torch.tensor(np.array(fut_list),    dtype=torch.float32),
        "static_feats_categorical": torch.tensor(static_list,           dtype=torch.long),
    }
    return batch, tickers, last_dts, last_enc_vals

def extract_interpretability(output, tickers, last_enc_vals):
    results = []
    for i, ticker in enumerate(tickers):
        # feature importance (unknown_past)
        hist_w  = output["historical_selection_weights"][i]        # [60, 55]
        avg_w   = hist_w.mean(dim=0).detach().numpy()              # [55]
        top5_idx= avg_w.argsort()[::-1][:5]
        top5_up = []
        for rank, idx in enumerate(top5_idx, 1):
            feat = HISTORICAL_COLS[idx]
            if feat in KNOWN_FUTURE_COLS:
                continue  # known_future는 아래서 별도 처리
            top5_up.append({
                "rank":          rank,
                "feature":       feat,
                "importance":    round(float(avg_w[idx]), 4),
                "current_value": round(float(last_enc_vals[i].get(feat, 0)), 6),
                "description":   FEATURE_DESC.get(feat, feat),
            })

        # known_future importance
        fut_w = output["future_selection_weights"][i].squeeze(0).detach().numpy()  # [5]
        known_future_imp = [
            {
                "feature":       feat,
                "importance":    round(float(fut_w[j]), 4),
                "current_value": round(float(last_enc_vals[i].get(feat, 0)), 4),
                "description":   FEATURE_DESC.get(feat, feat),
            }
            for j, feat in enumerate(KNOWN_FUTURE_COLS)
        ]

        # static importance
        static_w = output["static_weights"][i].detach().numpy()  # [2]
        static_imp = [
            {"feature": feat, "importance": round(float(static_w[j]), 4),
             "description": FEATURE_DESC.get(feat, feat)}
            for j, feat in enumerate(STATIC_COLS)
        ]

        # attention peak (encoder 60봉 기준 minutes_ago 변환)
        attn = output["attention_scores"][i, 0, :60].detach().numpy()  # [60]
        top5_attn_idx = attn.argsort()[::-1][:5]
        attn_peak = [
            {"minutes_ago": int((59 - idx) * 5 + 5),
             "attention":   round(float(attn[idx]), 4)}
            for idx in top5_attn_idx
        ]
        attn_peak.sort(key=lambda x: x["minutes_ago"])

        results.append({
            "top_features": {
                "unknown_past":  top5_up[:5],
                "known_future":  known_future_imp,
                "static":        static_imp,
            },
            "attention_peak": attn_peak,
        })
    return results

def run_once_inference(user_id: str, tickers: list, callback_url: str, job_id: str):
    today = date.today()
    try:
        df = load_features_for_once(tickers)
        if df is None:
            logger.error(f"[{job_id}] 피처 없음")
            return

        batch, valid_tickers, last_dts, last_enc_vals = build_batch_for_once(df)
        if batch is None:
            logger.error(f"[{job_id}] 배치 구성 실패")
            return

        model = get_model()
        with torch.no_grad():
            output = model(batch)

        logits = output["predicted_quantiles"].squeeze(1)
        probs  = F.softmax(logits, dim=-1)

        interp_list = extract_interpretability(output, valid_tickers, last_enc_vals)

        results = []
        for i, (ticker, trade_dt) in enumerate(zip(valid_tickers, last_dts)):
            p = probs[i].numpy()
            results.append({
                "ticker":          ticker,
                "trade_datetime":  trade_dt.isoformat(),
                "pred_label":      int(p.argmax()),
                "pred_str":        LABEL_MAP[int(p.argmax())],
                "prob_buy":        round(float(p[0]), 4),
                "prob_hold":       round(float(p[1]), 4),
                "prob_sell":       round(float(p[2]), 4),
                "interpretability": interp_list[i],
            })

        payload = {
            "job_id":       job_id,
            "user_id":      user_id,
            "inference_at": datetime.now().isoformat(),
            "results":      results,
        }

        if callback_url:
            headers = {"Content-Type": "application/json"}
            if AI_SERVER_API_KEY:
                headers["X-API-Key"] = AI_SERVER_API_KEY
            resp = requests.post(callback_url, json=payload, headers=headers, timeout=15)
            resp.raise_for_status()
            logger.info(f"[{job_id}] callback 전송 완료 → {resp.status_code}")

    except Exception as e:
        logger.error(f"[{job_id}] ONCE 추론 실패: {e}")


# ============================================================
# FastAPI 앱
# ============================================================
app = FastAPI(title="AI Inference Server")

class CommandRequest(BaseModel):
    command:      str
    user_id:      str
    tickers:      list[str] = []
    callback_url: str = ""


@app.post("/command")
async def handle_command(
    req: CommandRequest,
    background_tasks: BackgroundTasks,
    x_api_key: str = Header(default=""),
):
    if x_api_key != AI_SERVER_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # ── START ────────────────────────────────────────────────
    if req.command == "START":
        data = load_tickers()
        data["users"][req.user_id] = req.tickers
        all_t = sorted(set(t for ts in data["users"].values() for t in ts))
        data["all_tickers"] = all_t
        save_tickers(data)
        logger.info(f"START: {req.user_id} → {req.tickers} / 전체: {all_t}")
        return {
            "status": "ok", "command": "START",
            "user_id": req.user_id,
            "registered": req.tickers,
            "all_active_tickers": all_t,
        }

    # ── STOP ─────────────────────────────────────────────────
    elif req.command == "STOP":
        data = load_tickers()
        removed = data["users"].pop(req.user_id, [])
        all_t = sorted(set(t for ts in data["users"].values() for t in ts))
        data["all_tickers"] = all_t
        save_tickers(data)
        logger.info(f"STOP: {req.user_id} 제거 / 전체: {all_t}")
        return {
            "status": "ok", "command": "STOP",
            "user_id": req.user_id,
            "removed": removed,
            "all_active_tickers": all_t,
        }

    # ── ONCE ─────────────────────────────────────────────────
    elif req.command == "ONCE":
        job_id = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{req.user_id}"
        background_tasks.add_task(
            run_once_inference,
            req.user_id, req.tickers, req.callback_url, job_id,
        )
        logger.info(f"ONCE 접수: {job_id} / tickers={req.tickers}")
        return {"status": "accepted", "job_id": job_id, "estimated_seconds": 5}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown command: {req.command}")


@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now().isoformat()}
