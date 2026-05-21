# TFT 학습 데이터 전처리 가이드

> **기준**: 추론 파이프라인(`inference.py`)이 정답. 학습은 추론 기준에 맞춤.
> **라이브러리**: `tft-torch` (PlaytikaOSS) v0.0.6

---

## 1. 프로젝트 개요

- **모델**: Temporal Fusion Transformer — tft-torch 구현체
- **태스크**: 5분봉 기준 **1시간 후(12봉) 주가 방향 3진 분류** (매수/관망/매도)
- **대상**: KOSPI200 + KOSDAQ150 = 350종목 (ticker에 `Z` 포함 종목 제외)
- **학습 기간**: 2020-01-01 ~ 2023-12-31
- **검증 기간**: 2024-01-01 ~ 2024-12-31
- **테스트 기간**: 2025-01-01 ~ 2025-12-31

---

## 2. 고정 설정

### 라벨

```
horizon:   12봉 후 (1시간)
threshold: ±1.5%

future_return >= +1.5% → 매수 (0)
future_return <= -1.5% → 매도 (2)
그 외                  → 관망 (1)

유효 학습 봉: 09:05 ~ 14:20
  - 09:05: 첫 5분봉 완성 시각
  - 14:20: 이후 봉은 12봉 후(15:30~)가 장 마감 이후라 라벨 없음
```

### TFT 설정

```
encoder_length:      60봉 (5시간)
decoder_length:      1봉  (바로 다음 봉)
prediction_horizon:  12봉(1시간) 후 라벨 → decoder 1봉에 부착
state_size:          32
attention_heads:     4
lstm_layers:         1
output_size:         3  (매수/관망/매도 logit)
```

### 피처셋 (총 57개 고유 피처)

```
5분봉 실시간 Unknown Past (13개):
    rel_close, rel_high, rel_low, log_ret
    disparity_5, disparity_20, disparity_60
    vol_ratio, rsi_14, bb_position
    macd_ratio, macd_signal_ratio, macd_hist_ratio

일봉 기술적 Unknown Past (5개):
    log_ret_1d, disparity_5d, disparity_20d, disparity_60d, volatility_20d

수급 Unknown Past (3개):
    prop_individual, prop_foreign, prop_institution

기업가치 Unknown Past (4개):
    per, pbr, per_chg_1d, pbr_chg_1d

시장 매크로 Unknown Past (11개):
    kospi_ret, kosdaq_ret,
    snp500_ret, nasdaq_ret, phlx_semi_ret,
    vix_chg, usd_krw_chg,
    us_10y_yield_chg, rate_spread_us_kr,
    wti_ret, gold_ret

섹터 Unknown Past (6개):
    sector_ret_1d, sector_ret_5d, sector_ret_20d,
    sector_ma_ratio_20d, sector_volatility, sector_volume_ratio

종목 이벤트 Unknown Past (6개):
    is_dividend, is_bonus_issue, is_rights_offering,
    is_split, is_merger, is_earnings

기타 Unknown Past (2개):
    day_of_week, listing_days

Known Future (5개):
    time_progress, is_bok, is_fomc,
    is_witching_kr, is_witching_us

Static Categoricals (2개):
    sector_id (0~19), market_id (KOSDAQ=0, KOSPI=1)
```

> **구 가이드 대비 변경 사항**
> - 제거: `turnover_ratio`, `vkospi_chg`, `is_short_selling_banned`
> - Known Future 5번째: `is_short_selling_banned` → `is_witching_us`
> - 총 피처: 60개 → 57개

---

## 3. 라벨 정의

```python
df = df.sort_values(["ticker", "trade_datetime"])

# 12봉(1시간) 후 종가 수익률
df["future_close"]  = df.groupby("ticker")["close_price"].shift(-12)
df["future_return"] = (df["future_close"] - df["close_price"]) / df["close_price"]

def make_label(ret):
    if pd.isna(ret):
        return None
    if ret >= 0.015:
        return 0   # 매수
    elif ret <= -0.015:
        return 2   # 매도
    else:
        return 1   # 관망

df["label"] = df["future_return"].apply(make_label)
df = df[df["label"].notna()]

# 14:20 이후 봉 제거 (12봉 후 데이터 없음)
df = df[df["trade_datetime"].dt.time <= pd.Timestamp("14:20").time()]

# 라벨 생성 후 불필요 컬럼 제거
df = df.drop(columns=["close_price", "future_close", "future_return"])
```

---

## 4. 피처 목록

### 4-1. TFT 입력 분류 요약

| 분류 | 개수 | 역할 |
|---|---|---|
| Static Categoricals | 2 | 종목 고유 특성. encoder·decoder 전체에 영향 |
| Known Future (time_varying_known) | 5 | 미래 시점도 사전에 알 수 있는 피처. encoder + decoder 입력 |
| Unknown Past (time_varying_unknown) | 50 | 과거에만 관측 가능. encoder 입력에만 사용 |
| **tft-torch 입력** | | `historical_ts_numeric` [B,60,55] / `future_ts_numeric` [B,1,5] / `static_feats_categorical` [B,2] |

### 4-2. 5분봉 실시간 피처 (14개)

> time_progress는 Known Future로 분류됨

| 피처명 | 분류 | 설명 |
|---|---|---|
| `time_progress` | **Known Future** | 장중 경과 시간 비율 (09:00=0.0, 15:30=1.0) |
| `rel_close` | Unknown Past | 당일 시가 대비 현재가 비율 |
| `rel_high` | Unknown Past | 당일 시가 대비 고가 비율 |
| `rel_low` | Unknown Past | 당일 시가 대비 저가 비율 |
| `log_ret` | Unknown Past | 직전 봉 대비 로그 수익률 |
| `disparity_5` | Unknown Past | 현재가 / 5봉 이동평균 |
| `disparity_20` | Unknown Past | 현재가 / 20봉 이동평균 |
| `disparity_60` | Unknown Past | 현재가 / 60봉 이동평균 |
| `vol_ratio` | Unknown Past | 거래량 / 20봉 평균 거래량 |
| `rsi_14` | Unknown Past | RSI(14) |
| `bb_position` | Unknown Past | 볼린저밴드 위치 (0~1) |
| `macd_ratio` | Unknown Past | MACD / 현재가 |
| `macd_signal_ratio` | Unknown Past | MACD 시그널 / 현재가 |
| `macd_hist_ratio` | Unknown Past | MACD 히스토그램 / 현재가 |

### 4-3. Unknown Past 일봉 피처 (30개)

#### 일봉 기술적 (5개)
| 피처명 | 설명 |
|---|---|
| `log_ret_1d` | 전일 대비 로그 수익률 |
| `disparity_5d` | 5일 이동평균 이격도 |
| `disparity_20d` | 20일 이동평균 이격도 |
| `disparity_60d` | 60일 이동평균 이격도 |
| `volatility_20d` | 20일 역사적 변동성 |

> ⚠️ 제거: `turnover_ratio` (수집 파이프라인 미지원)

#### 수급 (3개)
| 피처명 | 설명 |
|---|---|
| `prop_individual` | 개인 순매수 / 시가총액 |
| `prop_foreign` | 외국인 순매수 / 시가총액 |
| `prop_institution` | 기관 순매수 / 시가총액 |

#### 기업가치 (4개)
| 피처명 | 설명 |
|---|---|
| `per` | PER (NULL ~20% → 0으로 채움) |
| `pbr` | PBR (NULL ~0.7% → 0으로 채움) |
| `per_chg_1d` | 전일 대비 PER 변화율 |
| `pbr_chg_1d` | 전일 대비 PBR 변화율 |

#### 시장 매크로 (11개)
| 피처명 | 설명 |
|---|---|
| `kospi_ret` | KOSPI 등락률 |
| `kosdaq_ret` | KOSDAQ 등락률 |
| `snp500_ret` | S&P500 등락률 (전일, +1일 처리) |
| `nasdaq_ret` | 나스닥 등락률 (전일, +1일 처리) |
| `phlx_semi_ret` | 필라델피아 반도체 등락률 |
| `vix_chg` | VIX 변동폭 |
| `usd_krw_chg` | 원달러 환율 변동 |
| `us_10y_yield_chg` | 미국채 10년물 금리 변동 |
| `rate_spread_us_kr` | 한미 금리차 |
| `wti_ret` | WTI 유가 등락률 |
| `gold_ret` | 금값 등락률 |

> ⚠️ 제거: `vkospi_chg` (수집 파이프라인 미지원)

#### 섹터 (6개)
| 피처명 | 설명 |
|---|---|
| `sector_ret_1d` | 섹터 1일 등락률 |
| `sector_ret_5d` | 섹터 5일 누적 등락률 |
| `sector_ret_20d` | 섹터 20일 누적 등락률 |
| `sector_ma_ratio_20d` | 섹터 20일 이평선 이격도 |
| `sector_volatility` | 섹터 장중 고저차 비율 |
| `sector_volume_ratio` | 섹터 거래량 / 20일 평균 |

#### 종목 이벤트 (6개)
| 피처명 | 설명 |
|---|---|
| `is_dividend` | 배당 공시 |
| `is_bonus_issue` | 무상증자 공시 |
| `is_rights_offering` | 유상증자 공시 |
| `is_split` | 액면분할 공시 |
| `is_merger` | 합병 공시 |
| `is_earnings` | 실적발표 공시 |

#### 기타 (2개)
| 피처명 | 설명 |
|---|---|
| `day_of_week` | 요일 (0=월 ~ 4=금) |
| `listing_days` | 상장 경과일 |

### 4-4. Known Future (5개)

| 피처명 | 설명 |
|---|---|
| `time_progress` | 장중 경과 시간 비율 |
| `is_bok` | 한국은행 금통위일 |
| `is_fomc` | FOMC 영향일 (미국 발표 +1일) |
| `is_witching_kr` | 한국 선물옵션 만기일 |
| `is_witching_us` | 미국 선물옵션 만기 영향일 (+1일) |

> ⚠️ 제거: `is_short_selling_banned` (구 가이드에 있었으나 현재 추론 파이프라인에 없음)

### 4-5. Static Categoricals (2개)

| 피처명 | 설명 | 카디널리티 |
|---|---|---|
| `sector_id` | 섹터 ID | 21 (0~19 유효, 20 = 미분류) |
| `market_id` | 시장 구분 | 3 (0=KOSDAQ, 1=KOSPI, 2 = 미분류) |

---

## 5. 학습 파이프라인

### 5-1. 결측치 처리

| 피처 | 결측 처리 |
|---|---|
| `per` | 0으로 채움 (적자기업) |
| `pbr` | 0으로 채움 |
| `prop_*` (수급) | 시가총액 0인 경우 0으로 채움 |
| 기타 NaN | 0으로 채움 |

### 5-2. 클리핑

> 추론 파이프라인은 현재 클리핑을 적용하지 않음.
> **학습에서 클리핑을 적용할 경우, 추론 파이프라인에도 동일 변환을 반드시 적용해야 함.**

| 피처 | 클리핑 범위 |
|---|---|
| `log_ret` | ±0.2 |
| `vol_ratio` | 0 ~ 10 |
| `disparity_5/20/60` | 0.7 ~ 1.3 |
| `macd_ratio/signal_ratio/hist_ratio` | train ±3σ |
| `prop_individual/foreign/institution` | ±0.3 |
| `vix_chg` | ±20 |
| `usd_krw_chg` | ±40 |
| `sector_volume_ratio` | 0 ~ 10 |

### 5-3. 스케일링

> 추론 파이프라인은 현재 스케일링을 적용하지 않음.
> **학습에서 스케일링을 적용할 경우, scaler를 저장하고 추론 파이프라인에도 동일 변환을 반드시 적용해야 함.**

| 피처 그룹 | 스케일링 |
|---|---|
| 연속형 피처 (대부분) | StandardScaler |
| `rsi_14` | MinMaxScaler (0~100 → 0~1) |
| `bb_position`, `time_progress` | 그대로 사용 (이미 [0,1]) |
| 0/1 플래그 피처 | 그대로 사용 |
| `sector_id`, `market_id` | Embedding (tft-torch 내부 처리) |

### 5-4. tft-torch 입력 포맷

```python
from tft_torch.tft import TemporalFusionTransformer
from omegaconf import OmegaConf
import torch

# inference.py와 동일한 설정 (반드시 일치시킬 것)
UNKNOWN_PAST_COLS = [
    # 5분봉 실시간 (13개)
    "rel_close", "rel_high", "rel_low", "log_ret",
    "disparity_5", "disparity_20", "disparity_60",
    "vol_ratio", "rsi_14", "bb_position",
    "macd_ratio", "macd_signal_ratio", "macd_hist_ratio",
    # 일봉 기술적 (5개)
    "log_ret_1d", "disparity_5d", "disparity_20d", "disparity_60d", "volatility_20d",
    # 수급 (3개)
    "prop_individual", "prop_foreign", "prop_institution",
    # 기업가치 (4개)
    "per", "pbr", "per_chg_1d", "pbr_chg_1d",
    # 매크로 (11개)
    "kospi_ret", "kosdaq_ret", "snp500_ret", "nasdaq_ret", "phlx_semi_ret",
    "vix_chg", "usd_krw_chg", "us_10y_yield_chg", "rate_spread_us_kr",
    "wti_ret", "gold_ret",
    # 섹터 (6개)
    "sector_ret_1d", "sector_ret_5d", "sector_ret_20d",
    "sector_ma_ratio_20d", "sector_volatility", "sector_volume_ratio",
    # 종목 이벤트 (6개)
    "is_dividend", "is_bonus_issue", "is_rights_offering",
    "is_split", "is_merger", "is_earnings",
    # 기타 (2개)
    "day_of_week", "listing_days",
]  # 50개

KNOWN_FUTURE_COLS = [
    "time_progress", "is_bok", "is_fomc", "is_witching_kr", "is_witching_us",
]  # 5개

HISTORICAL_COLS = UNKNOWN_PAST_COLS + KNOWN_FUTURE_COLS  # 55개 (순서 고정)
STATIC_COLS     = ["sector_id", "market_id"]             # 2개

TFT_CONFIG = OmegaConf.create({
    "task_type":           "regression",
    "target_window_start": None,
    "data_props": {
        "num_historical_numeric":          55,
        "num_historical_categorical":      0,
        "historical_categorical_cardinalities": [],
        "num_static_numeric":              0,
        "num_static_categorical":          2,
        "static_categorical_cardinalities": [21, 3],
        "num_future_numeric":              5,
        "num_future_categorical":          0,
        "future_categorical_cardinalities": [],
    },
    "model": {
        "state_size":       32,
        "attention_heads":  4,
        "dropout":          0.1,
        "lstm_layers":      1,
        "output_quantiles": [0.1, 0.5, 0.9],  # 3개 출력 → 3-class logit
    },
})
```

#### 샘플 준비 (ticker t 시점 기준)

```python
# 각 샘플: (ticker, 시점 t) 에서 encoder 60봉 + decoder 1봉 추출
#
# encoder: [t-60, t-1] → historical_ts_numeric [60, 55]
# decoder: [t]         → future_ts_numeric     [1,  5]
# label:   t의 label   → scalar (0/1/2), shift(-12)으로 미리 계산됨
#
# encoder에 들어가는 60봉은 이전 날 봉도 포함 가능
# (inference.py와 동일하게 5일치 이상 데이터 미리 로드 후 window 추출)

def make_sample(df_ticker, t_idx, encoder_len=60):
    """df_ticker: 단일 종목 전체 시계열 (시간 정순 정렬)"""
    if t_idx < encoder_len:
        return None  # encoder 데이터 부족

    hist = df_ticker.iloc[t_idx - encoder_len : t_idx]  # 60행
    fut  = df_ticker.iloc[t_idx : t_idx + 1]            # 1행

    historical_ts = hist[HISTORICAL_COLS].values.astype("float32")  # [60, 55]
    future_ts     = fut[KNOWN_FUTURE_COLS].values.astype("float32") # [1,  5]
    sector_id     = min(max(int(fut["sector_id"].iloc[0]), 0), 20)
    market_id     = min(max(int(fut["market_id"].iloc[0]), 0), 2)
    label         = int(fut["label"].iloc[0])

    return {
        "historical_ts_numeric":    torch.tensor(historical_ts),
        "future_ts_numeric":        torch.tensor(future_ts),
        "static_feats_categorical": torch.tensor([[sector_id, market_id]]),
        "label":                    torch.tensor([label]),
    }
```

#### 학습 루프 스케치

```python
import torch.nn.functional as F

model     = TemporalFusionTransformer(TFT_CONFIG)
optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

# 클래스 불균형 보정 (예상 분포: 매수 27% / 관망 45% / 매도 27%)
class_weights = torch.tensor([1.23, 0.74, 1.23])  # total / (3 × class_count) 근사
criterion = torch.nn.CrossEntropyLoss(weight=class_weights)

for batch in dataloader:
    hist   = batch["historical_ts_numeric"]     # [B, 60, 55]
    fut    = batch["future_ts_numeric"]          # [B, 1,  5]
    static = batch["static_feats_categorical"]   # [B, 2]
    labels = batch["label"].squeeze(-1)          # [B]

    output = model({
        "historical_ts_numeric":    hist,
        "future_ts_numeric":        fut,
        "static_feats_categorical": static,
    })

    logits = output["predicted_quantiles"].squeeze(1)  # [B, 3]
    loss   = criterion(logits, labels)

    optimizer.zero_grad()
    loss.backward()
    optimizer.step()

# 추론 시 softmax → 클래스 확률
probs      = F.softmax(logits, dim=-1)   # [B, 3]
pred_label = probs.argmax(dim=-1)        # [B]
```

#### 가중치 저장 (state_dict 형식)

```python
# inference.py가 state_dict 방식으로 로드하므로 반드시 state_dict로 저장
torch.save(model.state_dict(), "best_model_state_dict.pt")

# 로드 확인 (inference.py와 동일 방식)
model_check = TemporalFusionTransformer(TFT_CONFIG)
model_check.load_state_dict(torch.load("best_model_state_dict.pt", map_location="cpu"))
model_check.eval()
```

---

## 6. 데이터 파일 구성

```
csv_output/
├── features_2020.csv   # train (선택적)
├── features_2021.csv   # train (선택적)
├── features_2022.csv   # train (선택적)
├── features_2023.csv   # train
├── features_2024.csv   # validation
└── features_2025.csv   # test
```

**컬럼 구성 (CSV)**

| 컬럼 | 설명 |
|---|---|
| `ticker` | 종목 코드 |
| `trade_datetime` | 5분봉 datetime |
| `trade_date` | 날짜 |
| `label` | 라벨 (0/1/2), shift(-12) 적용 후 |
| `sector_id` | Static (0~19) |
| `market_id` | Static (0/1) |
| UNKNOWN_PAST_COLS (50개) | 시계열 피처 |
| KNOWN_FUTURE_COLS (5개) | 알려진 미래 피처 |

**주의**: `close_price`는 라벨 계산 후 즉시 제거. CSV에 포함하지 않음.

---

## 7. 클래스 불균형 처리

```
예상 클래스 분포:
매수 (0): 약 27%
관망 (1): 약 45%
매도 (2): 약 27%

처리 방법: CrossEntropyLoss weight 조정
weight[c] = total_samples / (num_classes × count[c])
```

---

## 8. 실험 계획

### 8-1. 학습 기간 실험

| 실험 | train | 비고 |
|---|---|---|
| A안 | 2023-01 ~ 2023-06 (6개월) | 먼저 구조/하이퍼파라미터 탐색 |
| B안 | 2023-01 ~ 2023-12 (1년) | 성능 확인 후 확장 |
| C안 | 2020-01 ~ 2023-12 (4년) | 로컬 GPU 환경 필요 |

### 8-2. 하이퍼파라미터 실험 (추론 검증 완료 후)

- `encoder_length`: 60 → 40, 80 등
- `state_size`: 32 → 64, 128
- `attention_heads`: 4 → 2, 8
- 라벨 threshold: ±1.5% → ±1.0%, ±2.0%
- 예측 horizon: 12봉(1시간) → 6봉(30분), 24봉(2시간)

---

## 9. 주요 주의사항

```
1. 추론 파이프라인 일치
   → 피처 목록, 순서(HISTORICAL_COLS), TFT_CONFIG가 inference.py와 반드시 동일해야 함
   → 클리핑/스케일링 적용 시 inference.py에도 동일 변환 추가 필요

2. 데이터 누수 방지
   → 라벨 계산 후 close_price 즉시 제거
   → 클리핑/스케일링은 train fit → val/test transform
   → 날짜 기준 시계열 분리 (랜덤 분리 금지)

3. 유효 학습 봉 범위
   → 09:05 ~ 14:20 (14:20 이후는 1시간 후 데이터 없음)
   → encoder 60봉은 이전 날 봉 포함 가능

4. 특수 종목 제외
   → ticker에 'Z' 포함 종목 제외 (보통주 아님)

5. Static 카디널리티
   → sector_id: 0~19 유효, 미분류는 20으로 clamp
   → market_id: 0=KOSDAQ, 1=KOSPI, 미분류는 2로 clamp

6. 가중치 저장 형식
   → torch.save(model.state_dict(), ...) 형식 사용
   → inference.py가 load_state_dict() 방식으로 로드
```
