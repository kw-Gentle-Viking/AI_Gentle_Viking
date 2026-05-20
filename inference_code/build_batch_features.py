"""
build_batch_features.py
=======================
장 마감 후 전체 종목 5분봉 피처 배치 생성 (하루 1회)
- 오늘 intraday_5min 데이터 기반으로 realtime_features 생성
- REALTIME_TICKERS 포함 전체 종목 대상

crontab:
    30 16 * * 1-5 /home/user/miniconda3/envs/kis_collector/bin/python /home/user/build_batch_features.py >> /home/user/build_batch_features.log 2>&1
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
        logging.FileHandler("build_batch_realtime.log"),
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

TODAY       = date.today()
WARMUP_DAYS = 14
MARKET_OPEN = datetime.combine(TODAY, datetime.strptime("09:00", "%H:%M").time())
TOTAL_MINUTES = 390.0


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


def load_tickers() -> list:
    """오늘 intraday_5min에 데이터 있는 종목 조회"""
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ticker FROM intraday_5min
        WHERE DATE(datetime) = %s
        ORDER BY ticker
    """, (TODAY,))
    tickers = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return tickers


def build_features_for_ticker(ticker: str, df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("datetime").reset_index(drop=True)

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].ffill()

    df["log_ret"]      = np.log(df["close"] / df["close"].shift(1))
    df["disparity_5"]  = df["close"] / df["close"].rolling(5).mean()
    df["disparity_20"] = df["close"] / df["close"].rolling(20).mean()
    df["disparity_60"] = df["close"] / df["close"].rolling(60).mean()
    df["vol_ma_20"]    = df["volume"].rolling(20).mean()
    df["vol_ratio"]    = df["volume"] / df["vol_ma_20"].replace(0, np.nan)
    df["rsi_14"]       = calc_rsi(df["close"], 14)

    bb_mid             = df["close"].rolling(20).mean()
    bb_std             = df["close"].rolling(20).std()
    bb_upper           = bb_mid + 2 * bb_std
    bb_lower           = bb_mid - 2 * bb_std
    df["bb_position"]  = (df["close"] - bb_lower) / (bb_upper - bb_lower).replace(0, np.nan)

    macd, macd_signal, macd_hist = calc_macd(df["close"])
    df["macd_ratio"]        = macd        / df["close"].replace(0, np.nan)
    df["macd_signal_ratio"] = macd_signal / df["close"].replace(0, np.nan)
    df["macd_hist_ratio"]   = macd_hist   / df["close"].replace(0, np.nan)

    df["trade_date"] = df["datetime"].dt.date

    # 날짜별 당일 시가
    df["day_open"] = df.groupby("trade_date")["open"].transform("first")

    # time_progress
    df["time_progress"] = df["datetime"].apply(
        lambda dt: max(0.0, min(1.0,
            (dt - datetime.combine(dt.date(), MARKET_OPEN.time())).seconds / 60 / TOTAL_MINUTES
        ))
    )

    # 당일 시가 대비
    df["rel_close"] = (df["close"] - df["day_open"]) / df["day_open"].replace(0, np.nan)
    df["rel_high"]  = (df["high"]  - df["day_open"]) / df["day_open"].replace(0, np.nan)
    df["rel_low"]   = (df["low"]   - df["day_open"]) / df["day_open"].replace(0, np.nan)

    # 오늘 봉만 추출
    today_df = df[df["trade_date"] == TODAY].copy()
    today_df["ticker"] = ticker

    return today_df[[
        "ticker", "datetime", "trade_date",
        "time_progress", "rel_close", "rel_high", "rel_low", "log_ret",
        "disparity_5", "disparity_20", "disparity_60",
        "vol_ratio", "rsi_14", "bb_position",
        "macd_ratio", "macd_signal_ratio", "macd_hist_ratio",
    ]]


def save_to_db(df: pd.DataFrame) -> int:
    if df.empty:
        return 0

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
        return len(rows)
    except Exception as e:
        conn.rollback()
        logger.error(f"저장 실패: {e}")
        return 0
    finally:
        cur.close()
        conn.close()


def main():
    # 주말 가드
    if TODAY.weekday() >= 5:
        logger.info(f"주말({TODAY}) → 건너뜀")
        return

    logger.info(f"===== 5분봉 피처 배치 생성 시작 ({TODAY}) =====")

    # intraday_5min에 오늘 데이터 있는지 확인
    tickers = [t for t in load_tickers() if "Z" not in t]
    if not tickers:
        logger.warning(f"오늘({TODAY}) intraday_5min 데이터 없음 → 배치 수집 완료 후 재실행 필요")
        return
    logger.info(f"대상 종목: {len(tickers)}개")

    # 워밍업 포함 데이터 로드
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT ticker, datetime, open, high, low, close, volume
        FROM intraday_5min
        WHERE DATE(datetime) >= %s
          AND ticker = ANY(%s)
        ORDER BY ticker, datetime
    """, (TODAY - timedelta(days=WARMUP_DAYS), tickers))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        logger.warning("데이터 없음")
        return

    df_all = pd.DataFrame(rows, columns=[
        "ticker", "datetime", "open", "high", "low", "close", "volume"
    ])
    df_all["datetime"] = pd.to_datetime(df_all["datetime"])

    total_saved = 0
    failed = []

    for ticker, grp in df_all.groupby("ticker"):
        try:
            feat_df = build_features_for_ticker(ticker, grp.copy())
            if feat_df.empty:
                continue
            saved = save_to_db(feat_df)
            total_saved += saved
        except Exception as e:
            logger.error(f"{ticker} 실패: {e}")
            failed.append(ticker)

    logger.info(f"===== 완료: {total_saved:,}행 저장 / 실패 {len(failed)}개 =====")


if __name__ == "__main__":
    main()