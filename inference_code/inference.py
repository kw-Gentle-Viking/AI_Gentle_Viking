"""
inference.py
============
TFT 모델 실시간 추론 (5분마다 실행)
tft-torch (PlaytikaOSS) 기반 커스텀 TFT 모델 사용

추론 흐름:
    1. inference_features (장외 피처) 로드
    2. realtime_features (5분봉 피처) 로드
    3. tft-torch 입력 형식으로 변환
    4. 모델 추론 (softmax → 3-class 확률)
    5. 결과 DB 저장

실행 방식:
    inference_pipeline.py 에서 subprocess로 호출됨
    crontab에 직접 등록되지 않음
"""

import os
import logging
import psycopg2
from psycopg2.extras import execute_values
import pandas as pd
import numpy as np
from datetime import date, datetime, timedelta
import torch
import torch.nn.functional as F
from omegaconf import OmegaConf
from tft_torch.tft import TemporalFusionTransformer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("inference.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================
# 설정값
# ============================================================
DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     os.environ.get("DB_PORT", 5432),
    "dbname":   os.environ.get("DB_NAME", "stock_db"),
    "user":     os.environ.get("DB_USER", "stock_user"),
    "password": os.environ.get("DB_PASSWORD", "your_password"),
}

MODEL_PATH    = os.environ.get("TFT_MODEL_PATH", "/home/user/best_model_state_dict.pt")
REALTIME_TICKERS = ["005930", "000660"]

TODAY          = date.today()
NOW            = datetime.now()
LOOKBACK_DATE  = TODAY - timedelta(days=5)   # 영업일 3일+ 확보 (encoder 60봉용)
ENCODER_LENGTH = 60

# ============================================================
# 피처 컬럼 정의
# ============================================================
INFERENCE_COLS = [
    "log_ret_1d", "disparity_5d", "disparity_20d", "disparity_60d",
    "volatility_20d",
    "prop_individual", "prop_foreign", "prop_institution",
    "per", "pbr", "per_chg_1d", "pbr_chg_1d",
    "kospi_ret", "kosdaq_ret", "snp500_ret", "nasdaq_ret", "phlx_semi_ret",
    "vix_chg", "usd_krw_chg", "us_10y_yield_chg",
    "rate_spread_us_kr", "wti_ret", "gold_ret",
    "sector_ret_1d", "sector_ret_5d", "sector_ret_20d",
    "sector_ma_ratio_20d", "sector_volatility", "sector_volume_ratio",
    "is_dividend", "is_bonus_issue", "is_rights_offering",
    "is_split", "is_merger", "is_earnings",
    "is_bok", "is_fomc",
    "is_witching_kr", "is_witching_us",
    "sector_id", "market_id", "day_of_week", "listing_days",
]

REALTIME_COLS = [
    "time_progress",
    "rel_close", "rel_high", "rel_low", "log_ret",
    "disparity_5", "disparity_20", "disparity_60",
    "vol_ratio", "rsi_14", "bb_position",
    "macd_ratio", "macd_signal_ratio", "macd_hist_ratio",
]

# TFT 입력 분류
KNOWN_FUTURE_COLS = ["time_progress", "is_bok", "is_fomc", "is_witching_kr", "is_witching_us"]
STATIC_COLS       = ["sector_id", "market_id"]

UNKNOWN_PAST_COLS = [c for c in REALTIME_COLS + INFERENCE_COLS
                     if c not in KNOWN_FUTURE_COLS and c not in STATIC_COLS]

# 히스토리컬 입력 순서: unknown_past(50) + known_future(5) = 55
HISTORICAL_COLS = UNKNOWN_PAST_COLS + KNOWN_FUTURE_COLS

LABEL_MAP = {0: "매수", 1: "관망", 2: "매도"}

# ============================================================
# tft-torch 모델 설정 (state_dict에서 역산한 하이퍼파라미터)
#   state_size=32      : multihead_attn.out.weight [32,32]
#   attention_heads=4  : multihead_attn.w_k.bias [128] = 4×32
#   lstm_layers=1      : bias_hh_l0만 존재, l1 없음
#   static_cat=[21,3]  : embedding_layers.0 [21,32], .1 [3,32]
#   num_historical=55  : historical_ts_selection.fc1 [32,1760] = 32×55
#   num_future=5       : future_ts_selection.fc2 [5,32]
#   output_size=3      : output_layer.weight [3,32]
# ============================================================
TFT_CONFIG = OmegaConf.create({
    "task_type":            "regression",
    "target_window_start":  None,
    "data_props": {
        "num_historical_numeric":          len(HISTORICAL_COLS),   # 55
        "num_historical_categorical":      0,
        "historical_categorical_cardinalities": [],
        "num_static_numeric":              0,
        "num_static_categorical":          2,
        "static_categorical_cardinalities": [21, 3],
        "num_future_numeric":              len(KNOWN_FUTURE_COLS), # 5
        "num_future_categorical":          0,
        "future_categorical_cardinalities": [],
    },
    "model": {
        "state_size":       32,
        "attention_heads":  4,
        "dropout":          0.1,
        "lstm_layers":      1,
        "output_quantiles": [0.1, 0.5, 0.9],  # 3개 → 3-class logits
    },
})


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


# ============================================================
# 1. 테이블 초기화
# ============================================================
def init_table():
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS inference_results (
            ticker          VARCHAR(10)      NOT NULL,
            trade_datetime  TIMESTAMP        NOT NULL,
            trade_date      DATE             NOT NULL,
            pred_label      INT,
            pred_str        VARCHAR(10),
            prob_buy        DOUBLE PRECISION,
            prob_hold       DOUBLE PRECISION,
            prob_sell       DOUBLE PRECISION,
            model_version   VARCHAR(50),
            created_at      TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (ticker, trade_datetime)
        );
    """)
    conn.commit()
    cur.close()
    conn.close()


# ============================================================
# 2. 피처 로드
# ============================================================
def load_features() -> pd.DataFrame:
    conn = get_conn()
    cur  = conn.cursor()
    placeholders = ','.join(['%s'] * len(REALTIME_TICKERS))

    # 장외 피처 — 가장 최근 적재된 날짜 사용
    # (장중에는 오늘치 미생성 → 어제치 사용, 16:30 이후에는 오늘치 사용)
    cur.execute(f"""
        SELECT MAX(trade_date) FROM inference_features
        WHERE ticker IN ({placeholders})
    """, REALTIME_TICKERS)
    inf_date = cur.fetchone()[0]

    if inf_date is None:
        logger.warning("inference_features 없음")
        cur.close()
        conn.close()
        return pd.DataFrame()

    logger.info(f"inference_features 기준일: {inf_date}")

    cur.execute(f"""
        SELECT ticker, {', '.join(INFERENCE_COLS)}
        FROM inference_features
        WHERE trade_date = %s AND ticker IN ({placeholders})
    """, [inf_date] + REALTIME_TICKERS)
    inf_rows = cur.fetchall()

    if not inf_rows:
        logger.warning(f"inference_features({inf_date}) 없음")
        cur.close()
        conn.close()
        return pd.DataFrame()

    df_inf = pd.DataFrame(inf_rows, columns=["ticker"] + INFERENCE_COLS)

    # 5분봉 피처 (encoder 60봉 확보를 위해 최근 5일치)
    cur.execute(f"""
        SELECT ticker, trade_datetime, {', '.join(REALTIME_COLS)}
        FROM realtime_features
        WHERE trade_date >= %s AND ticker IN ({placeholders})
        ORDER BY ticker, trade_datetime
    """, [LOOKBACK_DATE] + REALTIME_TICKERS)
    rt_rows = cur.fetchall()

    cur.close()
    conn.close()

    if not rt_rows:
        logger.warning("realtime_features 없음")
        return pd.DataFrame()

    df_rt = pd.DataFrame(rt_rows, columns=["ticker", "trade_datetime"] + REALTIME_COLS)
    df_rt["trade_datetime"] = pd.to_datetime(df_rt["trade_datetime"])

    # 장외 피처를 5분봉에 broadcast (ticker 기준 join)
    df = df_rt.merge(df_inf, on="ticker", how="left")
    logger.info(f"피처 로드: {len(df['ticker'].unique())}종목 / {len(df)}봉")
    return df


# ============================================================
# 3. tft-torch 배치 준비
# ============================================================
def prepare_batch(df: pd.DataFrame):
    """
    반환:
        batch      : tft-torch forward() 입력 dict
        tickers    : 배치 내 종목 순서 (batch 인덱스와 1:1 대응)
        last_dts   : 종목별 마지막 봉 datetime (decoder step)
    """
    df = df.sort_values(["ticker", "trade_datetime"]).reset_index(drop=True)

    # NaN → 0
    for col in HISTORICAL_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    hist_list   = []
    fut_list    = []
    static_list = []
    tickers     = []
    last_dts    = []

    for ticker in sorted(df["ticker"].unique()):
        grp = df[df["ticker"] == ticker].sort_values("trade_datetime")
        grp = grp.tail(ENCODER_LENGTH + 1)  # 60 encoder + 1 decoder

        if len(grp) < ENCODER_LENGTH + 1:
            logger.warning(f"{ticker}: 봉 부족 ({len(grp)}) → 스킵")
            continue

        hist = grp.iloc[:ENCODER_LENGTH]  # [60, ...]
        fut  = grp.iloc[ENCODER_LENGTH:]  # [1, ...]

        # historical_ts_numeric: [60, 55]
        hist_list.append(hist[HISTORICAL_COLS].values.astype(np.float32))

        # future_ts_numeric: [1, 5]
        fut_list.append(fut[KNOWN_FUTURE_COLS].values.astype(np.float32))

        # static_feats_categorical: sector_id(0~20), market_id(0~2) / NaN → 미분류로 처리
        sector_id = min(max(int(grp["sector_id"].fillna(20).iloc[-1]), 0), 20)
        market_id = min(max(int(grp["market_id"].fillna(2).iloc[-1]), 0), 2)
        static_list.append([sector_id, market_id])

        tickers.append(ticker)
        last_dts.append(grp["trade_datetime"].iloc[-1])

    if not tickers:
        return None, [], []

    batch = {
        "historical_ts_numeric":    torch.tensor(np.array(hist_list),   dtype=torch.float32),  # [B, 60, 55]
        "future_ts_numeric":        torch.tensor(np.array(fut_list),    dtype=torch.float32),  # [B, 1,  5]
        "static_feats_categorical": torch.tensor(static_list,           dtype=torch.long),     # [B, 2]
    }
    return batch, tickers, last_dts


# ============================================================
# 4. 모델 로드
# ============================================================
def load_model():
    if not os.path.exists(MODEL_PATH):
        logger.error(f"모델 파일 없음: {MODEL_PATH}")
        return None

    model = TemporalFusionTransformer(TFT_CONFIG)
    state_dict = torch.load(MODEL_PATH, map_location="cpu")
    model.load_state_dict(state_dict)
    model.eval()
    logger.info(f"모델 로드 완료: {os.path.basename(MODEL_PATH)}")
    return model


# ============================================================
# 5. 추론
# ============================================================
def run_inference(model, batch: dict) -> torch.Tensor:
    with torch.no_grad():
        output = model(batch)
    # predicted_quantiles: [B, 1, 3] → squeeze → [B, 3]
    logits = output["predicted_quantiles"].squeeze(1)
    probs  = F.softmax(logits, dim=-1)
    return probs  # [B, 3]


# ============================================================
# 6. 결과 저장
# ============================================================
def save_results(tickers: list, last_dts: list, probs: torch.Tensor):
    rows = []
    for i, (ticker, trade_dt) in enumerate(zip(tickers, last_dts)):
        p          = probs[i].numpy()
        pred_label = int(p.argmax())
        rows.append((
            ticker, trade_dt, TODAY,
            pred_label, LABEL_MAP[pred_label],
            float(p[0]), float(p[1]), float(p[2]),
            os.path.basename(MODEL_PATH),
        ))

    conn = get_conn()
    cur  = conn.cursor()
    try:
        execute_values(cur, """
            INSERT INTO inference_results (
                ticker, trade_datetime, trade_date,
                pred_label, pred_str,
                prob_buy, prob_hold, prob_sell,
                model_version
            ) VALUES %s
            ON CONFLICT (ticker, trade_datetime) DO UPDATE SET
                pred_label    = EXCLUDED.pred_label,
                pred_str      = EXCLUDED.pred_str,
                prob_buy      = EXCLUDED.prob_buy,
                prob_hold     = EXCLUDED.prob_hold,
                prob_sell     = EXCLUDED.prob_sell,
                model_version = EXCLUDED.model_version
        """, rows)
        conn.commit()
        logger.info(f"추론 결과 저장: {len(rows)}건")
        for r in rows:
            logger.info(
                f"  {r[0]} | {r[1]} | {r[4]} | "
                f"매수:{r[5]:.3f} 관망:{r[6]:.3f} 매도:{r[7]:.3f}"
            )
    except Exception as e:
        conn.rollback()
        logger.error(f"결과 저장 실패: {e}")
    finally:
        cur.close()
        conn.close()


# ============================================================
# 메인
# ============================================================
def main():
    if TODAY.weekday() >= 5:
        logger.info(f"주말({TODAY}) → 건너뜀")
        return

    logger.info(f"===== 추론 시작 ({NOW.strftime('%H:%M')}) 대상: {REALTIME_TICKERS} =====")

    init_table()

    model = load_model()
    if model is None:
        return

    df = load_features()
    if df.empty:
        logger.warning("피처 없음, 종료")
        return

    batch, tickers, last_dts = prepare_batch(df)
    if batch is None:
        logger.warning("유효 종목 없음, 종료")
        return

    probs = run_inference(model, batch)
    save_results(tickers, last_dts, probs)

    logger.info("===== 완료 =====")


if __name__ == "__main__":
    main()
