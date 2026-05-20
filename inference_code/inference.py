"""
inference.py
============
TFT 모델 실시간 추론 (5분마다 실행)

추론 흐름:
    1. inference_features (장외 피처) 로드
    2. realtime_features (5분봉 피처) 로드
    3. 두 피처 합치기
    4. 모델 추론
    5. 결과 DB 저장

추론 대상 종목 (테스트 단계 하드코딩 / 추후 백엔드에서 수신):
    REALTIME_TICKERS = ["005930", "000660"]

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

MODEL_PATH = os.environ.get("TFT_MODEL_PATH", "/home/user/checkpoints/tft_best.ckpt")

# 추론 대상 종목 (테스트 단계 하드코딩 / 추후 백엔드에서 수신)
REALTIME_TICKERS = ["005930", "000660"]

TODAY         = date.today()
NOW           = datetime.now()
LOOKBACK_DATE = TODAY - timedelta(days=5)  # 영업일 3일+ 확보 (encoder 60봉용)

# 장외 피처 컬럼 (inference_features)
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

# 5분봉 피처 컬럼 (realtime_features)
REALTIME_COLS = [
    "time_progress",
    "rel_close", "rel_high", "rel_low", "log_ret",
    "disparity_5", "disparity_20", "disparity_60",
    "vol_ratio", "rsi_14", "bb_position",
    "macd_ratio", "macd_signal_ratio", "macd_hist_ratio",
]

LABEL_MAP = {0: "매수", 1: "관망", 2: "매도"}


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
            ticker          VARCHAR(10)  NOT NULL,
            trade_datetime  TIMESTAMP    NOT NULL,
            trade_date      DATE         NOT NULL,
            pred_label      INT,          -- 0:매수 1:관망 2:매도
            pred_str        VARCHAR(10),  -- 매수/관망/매도
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
    """장외 피처 + 최신 5분봉 피처 합치기"""
    conn = get_conn()
    cur  = conn.cursor()

    # 장외 피처 (오늘치)
    placeholders = ','.join(['%s'] * len(REALTIME_TICKERS))
    cur.execute(f"""
        SELECT ticker, trade_date, {', '.join(INFERENCE_COLS)}
        FROM inference_features
        WHERE trade_date = %s AND ticker IN ({placeholders})
    """, [TODAY] + REALTIME_TICKERS)
    inf_rows = cur.fetchall()

    if not inf_rows:
        logger.warning(f"오늘({TODAY}) inference_features 없음")
        cur.close()
        conn.close()
        return pd.DataFrame()

    df_inf = pd.DataFrame(inf_rows, columns=["ticker", "trade_date"] + INFERENCE_COLS)

    # 5분봉 피처 (encoder 60봉 확보를 위해 최근 5일치 포함)
    cur.execute(f"""
        SELECT ticker, trade_datetime, trade_date, {', '.join(REALTIME_COLS)}
        FROM realtime_features
        WHERE trade_date >= %s AND ticker IN ({placeholders})
        ORDER BY ticker, trade_datetime
    """, [LOOKBACK_DATE] + REALTIME_TICKERS)
    rt_rows = cur.fetchall()

    cur.close()
    conn.close()

    if not rt_rows:
        logger.warning(f"오늘({TODAY}) realtime_features 없음")
        return pd.DataFrame()

    df_rt = pd.DataFrame(rt_rows, columns=["ticker", "trade_datetime", "trade_date"] + REALTIME_COLS)

    # 장외 피처를 5분봉 피처에 조인 (날짜 기준)
    df = df_rt.merge(df_inf.drop(columns=["trade_date"]), on="ticker", how="left")
    df["trade_datetime"] = pd.to_datetime(df["trade_datetime"])
    df["trade_date"]     = pd.to_datetime(df["trade_date"]).dt.date

    logger.info(f"피처 로드: {len(df['ticker'].unique())}종목 / {len(df)}봉")
    return df


# ============================================================
# 3. 모델 추론
# ============================================================
def run_inference(df: pd.DataFrame) -> pd.DataFrame:
    """TFT 모델로 추론"""
    if df.empty:
        return pd.DataFrame()

    if not os.path.exists(MODEL_PATH):
        logger.error(f"모델 파일 없음: {MODEL_PATH}")
        return pd.DataFrame()

    try:
        from pytorch_forecasting import TemporalFusionTransformer, TimeSeriesDataSet
        import torch

        model = TemporalFusionTransformer.load_from_checkpoint(MODEL_PATH)
        model.eval()

        ENCODER_LENGTH = 60
        KNOWN_FUTURE_COLS = [
            "time_progress",
            "is_bok", "is_fomc",
            "is_witching_kr", "is_witching_us",
        ]
        UNKNOWN_PAST_COLS = [c for c in REALTIME_COLS + INFERENCE_COLS
                             if c not in KNOWN_FUTURE_COLS
                             and c not in ("sector_id", "market_id")]
        STATIC_COLS = ["sector_id", "market_id"]

        # NaN → 0 (numpy NaN, inf 포함)
        import math
        def safe_fill(val):
            try:
                if val is None or math.isnan(float(val)) or math.isinf(float(val)):
                    return 0.0
            except (TypeError, ValueError):
                pass
            return val

        for col in REALTIME_COLS + INFERENCE_COLS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
                df[col] = df[col].apply(lambda x: 0.0 if (pd.isna(x) or x != x) else x)

        # sector_id, market_id 문자열 변환 (TFT categorical 요구사항)
        df["sector_id"] = df["sector_id"].fillna(-1).astype(int).astype(str)
        df["market_id"] = df["market_id"].fillna(0).astype(int).astype(str)

        # time_idx 생성 (유효 종목 필터링 전 초기 정렬용)
        df = df.sort_values(["ticker", "trade_datetime"]).reset_index(drop=True)
        df["time_idx"] = df.groupby("ticker").cumcount()

        # 유효 종목만 (encoder_length 이상)
        valid = df.groupby("ticker")["time_idx"].count()
        valid = valid[valid >= ENCODER_LENGTH].index.tolist()
        df = df[df["ticker"].isin(valid)]

        if df.empty:
            logger.warning("encoder_length 미달 종목만 있음")
            return pd.DataFrame()

        # 종목별 마지막 61봉만 사용 (encoder 60 + decoder 1)
        # → 종목당 정확히 1개의 예측 샘플이 생성되어 결과 매핑이 안정적
        df = (
            df.groupby("ticker", group_keys=False)
            .apply(lambda x: x.tail(ENCODER_LENGTH + 1))
            .reset_index(drop=True)
        )
        df["time_idx"] = df.groupby("ticker").cumcount()
        df["label"]    = 1

        dataset = TimeSeriesDataSet(
            df,
            time_idx="time_idx",
            target="label",
            group_ids=["ticker"],
            max_encoder_length=ENCODER_LENGTH,
            max_prediction_length=1,
            static_categoricals=STATIC_COLS,
            time_varying_known_reals=KNOWN_FUTURE_COLS,
            time_varying_unknown_reals=UNKNOWN_PAST_COLS,
            target_normalizer=None,
            add_relative_time_idx=True,
            add_target_scales=False,
            add_encoder_length=True,
        )

        from torch.utils.data import DataLoader
        loader = DataLoader(dataset, batch_size=32, shuffle=False, num_workers=0)

        results = []
        with torch.no_grad():
            for batch in loader:
                x, _ = batch
                pred = model(x)["prediction"]  # (B, 1, 3)
                probs = torch.softmax(pred.squeeze(1), dim=-1)  # (B, 3)
                pred_labels = probs.argmax(dim=-1)

                # 해당 배치의 ticker, trade_datetime 추출
                indices = x["groups"].squeeze(-1).cpu().numpy()
                for i, (label, prob) in enumerate(zip(pred_labels.cpu().numpy(),
                                                       probs.cpu().numpy())):
                    results.append({
                        "pred_label": int(label),
                        "prob_buy":   float(prob[0]),
                        "prob_hold":  float(prob[1]),
                        "prob_sell":  float(prob[2]),
                    })

        # 결과 매핑: tail(61) 필터 덕분에 종목당 샘플 1개, 정렬 순서 보장
        # df가 ticker 알파벳 오름차순으로 정렬되어 있고 DataLoader(shuffle=False)도 동일 순서
        ticker_order = df.groupby("ticker")["trade_datetime"].max().reset_index()
        ticker_order = ticker_order.sort_values("ticker").reset_index(drop=True)

        df_results = pd.DataFrame(results)
        if len(df_results) != len(ticker_order):
            logger.warning(f"결과 수 불일치: {len(df_results)} vs {len(ticker_order)}")
            return pd.DataFrame()

        df_results["ticker"]         = ticker_order["ticker"].values
        df_results["trade_datetime"] = ticker_order["trade_datetime"].values
        df_results["trade_date"]     = TODAY
        df_results["pred_str"]       = df_results["pred_label"].map(LABEL_MAP)
        df_results["model_version"]  = os.path.basename(MODEL_PATH)

        return df_results

    except Exception as e:
        logger.error(f"추론 실패: {e}")
        return pd.DataFrame()


# ============================================================
# 4. 결과 저장
# ============================================================
def save_results(df: pd.DataFrame):
    if df.empty:
        return

    conn = get_conn()
    cur  = conn.cursor()
    try:
        rows = [
            (
                row["ticker"], row["trade_datetime"], row["trade_date"],
                int(row["pred_label"]), row["pred_str"],
                float(row["prob_buy"]), float(row["prob_hold"]), float(row["prob_sell"]),
                row["model_version"],
            )
            for _, row in df.iterrows()
        ]
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

        # 결과 출력
        for _, row in df.iterrows():
            logger.info(
                f"  {row['ticker']} | {row['trade_datetime']} | "
                f"{row['pred_str']} | "
                f"매수:{row['prob_buy']:.3f} 관망:{row['prob_hold']:.3f} 매도:{row['prob_sell']:.3f}"
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
    # 주말 가드
    if TODAY.weekday() >= 5:
        logger.info(f"오늘은 주말({TODAY}) → 실행 건너뜀")
        return

    logger.info(f"===== 추론 시작 ({NOW.strftime('%H:%M')}) 대상: {REALTIME_TICKERS} =====")

    init_table()

    df = load_features()
    if df.empty:
        logger.warning("피처 없음, 종료")
        return

    df_results = run_inference(df)
    if df_results.empty:
        logger.warning("추론 결과 없음")
        return

    save_results(df_results)
    logger.info("===== 완료 =====")


if __name__ == "__main__":
    main()