"""
load_5min_csv.py
================
5분봉 CSV 파일 → intraday_5min 테이블 적재
- KOSPI_{ticker}_5min_data_2026.csv
- KOSDAQ_{ticker}_5min_data_2026.csv

실행:
    python ~/load_5min_csv.py
"""

import os
import glob
import logging
import psycopg2
from psycopg2.extras import execute_values
import pandas as pd
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("load_5min_csv.log"),
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

CSV_DIR = "/home/user"


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"ticker": str})
    df["ticker"] = df["ticker"].str.strip().str.zfill(6)

    # trade_date + trade_time → datetime
    df["datetime"] = pd.to_datetime(
        df["trade_date"].astype(str) + " " + df["trade_time"].astype(str),
        format="%Y-%m-%d %H:%M:%S",
        errors="coerce"
    )
    df = df.dropna(subset=["datetime"])

    # 15:25 봉 제외
    df = df[~((df["datetime"].dt.hour == 15) & (df["datetime"].dt.minute == 25))]

    # 필요한 컬럼만
    df = df[["ticker", "datetime", "open_price", "high_price", "low_price", "close_price", "volume"]]
    df.columns = ["ticker", "datetime", "open", "high", "low", "close", "volume"]

    # 타입 변환
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    return df


def save_to_db(df: pd.DataFrame, ticker: str) -> int:
    if df.empty:
        return 0

    rows = [
        (row["ticker"], row["datetime"], row["open"], row["high"],
         row["low"], row["close"], row["volume"])
        for _, row in df.iterrows()
    ]

    conn = get_conn()
    cur  = conn.cursor()
    try:
        execute_values(cur, """
            INSERT INTO intraday_5min (ticker, datetime, open, high, low, close, volume)
            VALUES %s
            ON CONFLICT (ticker, datetime) DO UPDATE SET
                open   = EXCLUDED.open,
                high   = EXCLUDED.high,
                low    = EXCLUDED.low,
                close  = EXCLUDED.close,
                volume = EXCLUDED.volume
        """, rows)
        conn.commit()
        return len(rows)
    except Exception as e:
        conn.rollback()
        logger.error(f"{ticker} 저장 실패: {e}")
        return 0
    finally:
        cur.close()
        conn.close()


def main():
    logger.info("===== 5분봉 CSV 적재 시작 =====")

    # CSV 파일 목록
    patterns = [
        os.path.join(CSV_DIR, "KOSPI_*_5min_data_2026.csv"),
        os.path.join(CSV_DIR, "KOSDAQ_*_5min_data_2026.csv"),
    ]

    files = []
    for pattern in patterns:
        files.extend(glob.glob(pattern))
    files = sorted(set(files))

    logger.info(f"CSV 파일 {len(files)}개 발견")

    total_saved = 0
    failed = []

    for path in files:
        fname   = os.path.basename(path)
        ticker  = fname.split("_")[1]

        try:
            df = load_csv(path)
            if df.empty:
                logger.warning(f"{fname}: 데이터 없음")
                continue

            saved = save_to_db(df, ticker)
            total_saved += saved
            logger.info(f"{fname}: {saved:,}행 저장")

        except Exception as e:
            logger.error(f"{fname}: 실패 - {e}")
            failed.append(fname)

    logger.info(f"===== 완료: 총 {total_saved:,}행 저장 / 실패 {len(failed)}개 =====")
    if failed:
        for f in failed:
            logger.error(f"  실패: {f}")


if __name__ == "__main__":
    main()