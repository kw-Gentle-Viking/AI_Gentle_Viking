"""
init_realtime_features.py
=========================
5분봉 피처 초기 적재 (1회 실행)
intraday_5min 전체 데이터 → realtime_features 테이블

실행:
    conda activate kis_collector
    python ~/init_realtime_features.py
"""

import os
import logging
import psycopg2
from psycopg2.extras import execute_values
import pandas as pd
import numpy as np
from datetime import date, datetime
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("init_realtime_features.log"),
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

MARKET_OPEN  = "09:00:00"
MARKET_CLOSE = "15:30:00"
TOTAL_MINUTES = 390.0


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def nan_to_none(val):
    if val is None:
        return None
    try:
        import math
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
    logger.info("테이블 초기화 완료")


def load_tickers() -> list:
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT DISTINCT ticker FROM intraday_5min ORDER BY ticker")
    tickers = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    logger.info(f"전체 종목: {len(tickers)}개")
    return tickers


def build_features_for_ticker(ticker: str, df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("datetime").reset_index(drop=True)

    # ffill
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].ffill()

    # 피처 계산
    df["log_ret"]      = np.log(df["close"] / df["close"].shift(1))
    df["disparity_5"]  = df["close"] / df["close"].rolling(5).mean()
    df["disparity_20"] = df["close"] / df["close"].rolling(20).mean()
    df["disparity_60"] = df["close"] / df["close"].rolling(60).mean()
    df["vol_ma_20"]    = df["volume"].rolling(20).mean()
    df["vol_ratio"]    = df["volume"] / df["vol_ma_20"].replace(0, np.nan)
    df["rsi_14"]       = calc_rsi(df["close"], 14)

    # 볼린저밴드
    bb_mid            = df["close"].rolling(20).mean()
    bb_std            = df["close"].rolling(20).std()
    bb_upper          = bb_mid + 2 * bb_std
    bb_lower          = bb_mid - 2 * bb_std
    df["bb_position"] = (df["close"] - bb_lower) / (bb_upper - bb_lower).replace(0, np.nan)

    # MACD
    macd, macd_signal, macd_hist = calc_macd(df["close"])
    df["macd_ratio"]        = macd        / df["close"].replace(0, np.nan)
    df["macd_signal_ratio"] = macd_signal / df["close"].replace(0, np.nan)
    df["macd_hist_ratio"]   = macd_hist   / df["close"].replace(0, np.nan)

    # 날짜별 당일 시가 계산
    df["trade_date"] = df["datetime"].dt.date
    df["day_open"]   = df.groupby("trade_date")["open"].transform("first")

    # time_progress
    def calc_time_progress(dt):
        market_open = datetime.combine(dt.date(), datetime.strptime(MARKET_OPEN, "%H:%M:%S").time())
        elapsed = (dt - market_open).seconds / 60
        return max(0.0, min(1.0, elapsed / TOTAL_MINUTES))

    df["time_progress"] = df["datetime"].apply(calc_time_progress)

    # 당일 시가 대비
    df["rel_close"] = (df["close"] - df["day_open"]) / df["day_open"].replace(0, np.nan)
    df["rel_high"]  = (df["high"]  - df["day_open"]) / df["day_open"].replace(0, np.nan)
    df["rel_low"]   = (df["low"]   - df["day_open"]) / df["day_open"].replace(0, np.nan)

    df["ticker"] = ticker
    return df[[
        "ticker", "datetime", "trade_date",
        "time_progress", "rel_close", "rel_high", "rel_low", "log_ret",
        "disparity_5", "disparity_20", "disparity_60",
        "vol_ratio", "rsi_14", "bb_position",
        "macd_ratio", "macd_signal_ratio", "macd_hist_ratio",
    ]]


def save_to_db(conn, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
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
    cur = conn.cursor()
    try:
        execute_values(cur, """
            INSERT INTO realtime_features (
                ticker, trade_datetime, trade_date,
                time_progress, rel_close, rel_high, rel_low, log_ret,
                disparity_5, disparity_20, disparity_60,
                vol_ratio, rsi_14, bb_position,
                macd_ratio, macd_signal_ratio, macd_hist_ratio
            ) VALUES %s
            ON CONFLICT (ticker, trade_datetime) DO NOTHING
        """, rows)
        conn.commit()
        return len(rows)
    except Exception as e:
        conn.rollback()
        logger.error(f"저장 실패: {e}")
        return 0
    finally:
        cur.close()


def main():
    logger.info("===== 5분봉 피처 초기 적재 시작 =====")
    init_table()
    tickers = load_tickers()

    conn_read  = get_conn()
    conn_write = get_conn()
    cur_read   = conn_read.cursor()

    total_saved = 0
    failed = []

    for ticker in tqdm(tickers, desc="5분봉 피처 생성"):
        try:
            cur_read.execute("""
                SELECT ticker, datetime, open, high, low, close, volume
                FROM intraday_5min
                WHERE ticker = %s
                ORDER BY datetime
            """, (ticker,))
            rows = cur_read.fetchall()
            if not rows:
                continue

            df = pd.DataFrame(rows, columns=[
                "ticker", "datetime", "open", "high", "low", "close", "volume"
            ])
            df["datetime"] = pd.to_datetime(df["datetime"])

            feat_df = build_features_for_ticker(ticker, df)
            saved   = save_to_db(conn_write, feat_df)
            total_saved += saved

        except Exception as e:
            logger.error(f"{ticker} 실패: {e}")
            failed.append(ticker)

    cur_read.close()
    conn_read.close()
    conn_write.close()

    logger.info(f"===== 완료: {total_saved:,}행 저장 / 실패 {len(failed)}개 =====")


if __name__ == "__main__":
    main()