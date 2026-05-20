"""
build_realtime_features.py
==========================
5분봉 실시간 피처 생성 (장중 5분마다 실행)
intraday_5min 테이블에서 당일 데이터 읽어서 피처 계산

수집 피처 (14개):
    time_progress, rel_close, rel_high, rel_low, log_ret
    disparity_5, disparity_20, disparity_60
    vol_ratio, rsi_14, bb_position
    macd_ratio, macd_signal_ratio, macd_hist_ratio

출력:
    realtime_features 테이블 (5분마다 upsert)

실행 방식:
    run_pipeline.py 에서 장중(09:00~15:30)에 subprocess로 호출됨
    crontab에 직접 등록되지 않음
"""

import os
import math
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
        logging.FileHandler("build_realtime_features.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     os.environ.get("DB_PORT", 5432),
    "dbname":   os.environ.get("DB_NAME", "stock_db"),
    "user":     os.environ.get("DB_USER", "stock_user"),
    "password": os.environ.get("DB_PASSWORD", "your_password"),
}

TODAY        = date.today()
MARKET_OPEN  = datetime.combine(TODAY, datetime.strptime("09:00", "%H:%M").time())
MARKET_CLOSE = datetime.combine(TODAY, datetime.strptime("15:30", "%H:%M").time())
TOTAL_MINUTES = (MARKET_CLOSE - MARKET_OPEN).seconds / 60  # 390분

# disparity_60 계산을 위해 충분한 워밍업 필요
# 5분봉 78봉/일 × 2주(10거래일) = 780봉 > 60봉 충분
WARMUP_DAYS = 14


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def nan_to_none(val):
    if val is None:
        return None
    try:
        if math.isnan(float(val)) or math.isinf(float(val)):
            return None
    except (TypeError, ValueError):
        pass
    return val


def init_table():
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS realtime_features (
            ticker            VARCHAR(10) NOT NULL,
            trade_datetime    TIMESTAMP   NOT NULL,
            trade_date        DATE        NOT NULL,
            time_progress     DOUBLE PRECISION,
            rel_close         DOUBLE PRECISION,
            rel_high          DOUBLE PRECISION,
            rel_low           DOUBLE PRECISION,
            log_ret           DOUBLE PRECISION,
            disparity_5       DOUBLE PRECISION,
            disparity_20      DOUBLE PRECISION,
            disparity_60      DOUBLE PRECISION,
            vol_ratio         DOUBLE PRECISION,
            rsi_14            DOUBLE PRECISION,
            bb_position       DOUBLE PRECISION,
            macd_ratio        DOUBLE PRECISION,
            macd_signal_ratio DOUBLE PRECISION,
            macd_hist_ratio   DOUBLE PRECISION,
            PRIMARY KEY (ticker, trade_datetime)
        );
    """)
    conn.commit()
    cur.close()
    conn.close()


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs  = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast    = series.ewm(span=fast,   adjust=False).mean()
    ema_slow    = series.ewm(span=slow,   adjust=False).mean()
    macd        = ema_fast - ema_slow
    macd_signal = macd.ewm(span=signal,   adjust=False).mean()
    macd_hist   = macd - macd_signal
    return macd, macd_signal, macd_hist


def build_realtime_features() -> pd.DataFrame:
    conn = get_conn()
    cur  = conn.cursor()

    # 워밍업 포함 조회 (timedelta 사용, pd.Timedelta 아님)
    cur.execute("""
        SELECT ticker, datetime, open, high, low, close, volume
        FROM intraday_5min
        WHERE datetime >= %s
        ORDER BY ticker, datetime
    """, (TODAY - timedelta(days=WARMUP_DAYS),))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        logger.warning("intraday_5min 데이터 없음")
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=[
        "ticker", "datetime", "open", "high", "low", "close", "volume"
    ])
    df["datetime"]   = pd.to_datetime(df["datetime"])
    df["trade_date"] = df["datetime"].dt.date

    results = []
    for ticker, grp in df.groupby("ticker"):
        grp = grp.sort_values("datetime").reset_index(drop=True)

        # 당일 데이터 확인
        today_grp = grp[grp["trade_date"] == TODAY]
        if today_grp.empty:
            continue

        day_open = today_grp.iloc[0]["open"]
        if day_open == 0:
            continue

        # 피처 계산 (워밍업 포함 전체 기준)
        grp["log_ret"]      = np.log(grp["close"] / grp["close"].shift(1))
        grp["disparity_5"]  = grp["close"] / grp["close"].rolling(5).mean()
        grp["disparity_20"] = grp["close"] / grp["close"].rolling(20).mean()
        grp["disparity_60"] = grp["close"] / grp["close"].rolling(60).mean()
        grp["vol_ma_20"]    = grp["volume"].rolling(20).mean()
        grp["vol_ratio"]    = grp["volume"] / grp["vol_ma_20"].replace(0, np.nan)
        grp["rsi_14"]       = calc_rsi(grp["close"], 14)

        # 볼린저밴드
        bb_mid             = grp["close"].rolling(20).mean()
        bb_std             = grp["close"].rolling(20).std()
        bb_upper           = bb_mid + 2 * bb_std
        bb_lower           = bb_mid - 2 * bb_std
        grp["bb_position"] = (grp["close"] - bb_lower) / (bb_upper - bb_lower).replace(0, np.nan)

        # MACD
        macd, macd_signal, macd_hist = calc_macd(grp["close"])
        grp["macd_ratio"]        = macd        / grp["close"].replace(0, np.nan)
        grp["macd_signal_ratio"] = macd_signal / grp["close"].replace(0, np.nan)
        grp["macd_hist_ratio"]   = macd_hist   / grp["close"].replace(0, np.nan)

        # 당일 봉만 추출
        today_grp = grp[grp["trade_date"] == TODAY].copy()

        # time_progress
        today_grp["time_progress"] = today_grp["datetime"].apply(
            lambda dt: max(0.0, min(1.0, (dt - MARKET_OPEN).seconds / 60 / TOTAL_MINUTES))
        )

        # 당일 시가 대비
        today_grp["rel_close"] = (today_grp["close"] - day_open) / day_open
        today_grp["rel_high"]  = (today_grp["high"]  - day_open) / day_open
        today_grp["rel_low"]   = (today_grp["low"]   - day_open) / day_open

        today_grp["ticker"] = ticker
        results.append(today_grp[[
            "ticker", "datetime", "trade_date",
            "time_progress", "rel_close", "rel_high", "rel_low", "log_ret",
            "disparity_5", "disparity_20", "disparity_60",
            "vol_ratio", "rsi_14", "bb_position",
            "macd_ratio", "macd_signal_ratio", "macd_hist_ratio",
        ]])

    if not results:
        return pd.DataFrame()

    return pd.concat(results, ignore_index=True)


def save_to_db(df: pd.DataFrame):
    if df.empty:
        logger.warning("저장할 데이터 없음")
        return

    # NaN → None (numpy NaN 포함)
    for col in df.select_dtypes(include=[float]).columns:
        df[col] = df[col].apply(nan_to_none)
    df = df.where(pd.notna(df), None)

    rows = [
        (
            row["ticker"], row["datetime"], row["trade_date"],
            nan_to_none(row["time_progress"]), nan_to_none(row["rel_close"]),
            nan_to_none(row["rel_high"]),  nan_to_none(row["rel_low"]),
            nan_to_none(row["log_ret"]),
            nan_to_none(row["disparity_5"]), nan_to_none(row["disparity_20"]),
            nan_to_none(row["disparity_60"]),
            nan_to_none(row["vol_ratio"]), nan_to_none(row["rsi_14"]),
            nan_to_none(row["bb_position"]),
            nan_to_none(row["macd_ratio"]), nan_to_none(row["macd_signal_ratio"]),
            nan_to_none(row["macd_hist_ratio"]),
        )
        for _, row in df.iterrows()
    ]

    conn = get_conn()
    cur  = conn.cursor()
    try:
        execute_values(cur, """
            INSERT INTO realtime_features (
                ticker, trade_datetime, trade_date,
                time_progress, rel_close, rel_high, rel_low, log_ret,
                disparity_5, disparity_20, disparity_60,
                vol_ratio, rsi_14, bb_position,
                macd_ratio, macd_signal_ratio, macd_hist_ratio
            ) VALUES %s
            ON CONFLICT (ticker, trade_datetime) DO UPDATE SET
                time_progress     = EXCLUDED.time_progress,
                rel_close         = EXCLUDED.rel_close,
                rel_high          = EXCLUDED.rel_high,
                rel_low           = EXCLUDED.rel_low,
                log_ret           = EXCLUDED.log_ret,
                disparity_5       = EXCLUDED.disparity_5,
                disparity_20      = EXCLUDED.disparity_20,
                disparity_60      = EXCLUDED.disparity_60,
                vol_ratio         = EXCLUDED.vol_ratio,
                rsi_14            = EXCLUDED.rsi_14,
                bb_position       = EXCLUDED.bb_position,
                macd_ratio        = EXCLUDED.macd_ratio,
                macd_signal_ratio = EXCLUDED.macd_signal_ratio,
                macd_hist_ratio   = EXCLUDED.macd_hist_ratio
        """, rows)
        conn.commit()
        logger.info(f"저장 완료: {len(rows)}행 → realtime_features")
    except Exception as e:
        conn.rollback()
        logger.error(f"저장 실패: {e}")
    finally:
        cur.close()
        conn.close()


def main():
    now = datetime.now()

    # 주말 가드
    if TODAY.weekday() >= 5:
        logger.info(f"오늘은 주말({TODAY}) → 실행 건너뜀")
        return

    # 장 시간 가드 (09:00 ~ 15:35)
    if not (MARKET_OPEN <= now <= MARKET_CLOSE + timedelta(minutes=5)):
        logger.info(f"장외 시간({now.strftime('%H:%M')}) → 실행 건너뜀")
        return

    logger.info(f"===== 5분봉 피처 생성 ({now.strftime('%H:%M')}) =====")
    init_table()
    df = build_realtime_features()

    if df.empty:
        logger.warning("피처 없음")
        return

    logger.info(f"계산 완료: {len(df['ticker'].unique())}종목 / {len(df)}봉")
    save_to_db(df)
    logger.info("===== 완료 =====")


if __name__ == "__main__":
    main()