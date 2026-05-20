"""
load_5min_data.py
======================
대신증권 CYBOS Plus로 수집한 5분봉 CSV → PostgreSQL 적재

CSV 컬럼: ticker, trade_date, trade_time, open_price, high_price,
          low_price, close_price, volume, turnover, bid_size_total, ask_size_total
DB 저장:  ticker, trade_datetime, open_price, high_price, low_price, close_price, volume

파일명 형식:
    KOSPI_A{ticker}_5min_data.csv  (200개)
    KOSDAQ_A{ticker}_5min_data.csv (150개)

실행:
    conda activate kis_collector
    python load_5min_csv_to_db.py
"""

import os
import glob
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from tqdm import tqdm
import logging
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

# CSV 파일이 있는 디렉토리
CSV_DIR = "/home/user/"

# 적재 기간 필터 (이 날짜 이후 데이터만 적재)
START_DATE = "2026-01-01"

# INSERT 배치 크기
BATCH_SIZE = 5000


# ============================================================
# 1. 테이블 생성
# ============================================================
def init_table(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS intraday_5min (
            ticker      VARCHAR(6)   NOT NULL,
            datetime    TIMESTAMP    NOT NULL,
            open        INTEGER      NOT NULL,
            high        INTEGER      NOT NULL,
            low         INTEGER      NOT NULL,
            close       INTEGER      NOT NULL,
            volume      BIGINT       NOT NULL,
            PRIMARY KEY (ticker, datetime)
        );
    """)
    conn.commit()
    cur.close()
    logger.info("테이블 확인 완료")


# ============================================================
# 2. CSV 파일 목록 수집
# ============================================================
def get_csv_files() -> list:
    kospi_files  = sorted(glob.glob(os.path.join(CSV_DIR, "KOSPI_*.csv")))
    kosdaq_files = sorted(glob.glob(os.path.join(CSV_DIR, "KOSDAQ_*.csv")))
    all_files = kospi_files + kosdaq_files
    logger.info(f"KOSPI: {len(kospi_files)}개 / KOSDAQ: {len(kosdaq_files)}개 / 전체: {len(all_files)}개")
    return all_files


# ============================================================
# 3. CSV 읽기 및 전처리
# ============================================================
def load_csv(filepath: str) -> pd.DataFrame:
    df = pd.read_csv(filepath, dtype={
        "ticker":      str,
        "trade_date":  str,
        "trade_time":  str,
        "open_price":  float,
        "high_price":  float,
        "low_price":   float,
        "close_price": float,
        "volume":      float,
    })

    # 필요한 컬럼만 선택
    df = df[["ticker", "trade_date", "trade_time",
             "open_price", "high_price", "low_price", "close_price", "volume"]]

    # ticker 6자리 정규화
    df["ticker"] = df["ticker"].str.strip().str.zfill(6)

    # trade_datetime 생성 (trade_date + trade_time 합치기)
    df["datetime"] = pd.to_datetime(
        df["trade_date"] + " " + df["trade_time"],
        format="%Y-%m-%d %H:%M:%S"
    )

    # 적재 기간 필터
    df = df[df["datetime"] >= START_DATE]

    # 거래 시간 필터 (09:00 ~ 15:30)
    time_only = df["datetime"].dt.time
    df = df[
        (time_only >= pd.Timestamp("09:00:00").time()) &
        (time_only <= pd.Timestamp("15:30:00").time())
    ]

    # 거래량 0 제거 (거래정지)
    df = df[df["volume"] > 0]

    # 정수 변환
    df["open"]   = df["open_price"].astype(int)
    df["high"]   = df["high_price"].astype(int)
    df["low"]    = df["low_price"].astype(int)
    df["close"]  = df["close_price"].astype(int)
    df["volume"] = df["volume"].astype(int)

    # 중복 제거
    df = df.drop_duplicates(subset=["ticker", "datetime"])

    # 정렬
    df = df.sort_values("datetime").reset_index(drop=True)

    return df[["ticker", "datetime", "open", "high", "low", "close", "volume"]]


# ============================================================
# 4. DB 저장
# ============================================================
def save_to_db(conn, df: pd.DataFrame) -> int:
    if df.empty:
        return 0

    rows = [
        (row["ticker"], row["datetime"],
         row["open"], row["high"], row["low"], row["close"], row["volume"])
        for _, row in df.iterrows()
    ]

    cur = conn.cursor()
    try:
        execute_values(cur, """
            INSERT INTO intraday_5min
                (ticker, datetime, open, high, low, close, volume)
            VALUES %s
            ON CONFLICT (ticker, datetime) DO NOTHING
        """, rows, page_size=BATCH_SIZE)
        conn.commit()
        return len(rows)
    except Exception as e:
        conn.rollback()
        logger.error(f"저장 실패: {e}")
        return 0
    finally:
        cur.close()


# ============================================================
# 메인
# ============================================================
def main():
    logger.info("===== 5분봉 CSV 적재 시작 =====")
    start_time = datetime.now()

    conn = psycopg2.connect(**DB_CONFIG)
    init_table(conn)

    files = get_csv_files()
    if not files:
        logger.error(f"CSV 파일 없음: {CSV_DIR}")
        return

    total_saved = 0
    failed = []

    for filepath in tqdm(files, desc="CSV 적재"):
        filename = os.path.basename(filepath)
        try:
            df = load_csv(filepath)
            if df.empty:
                logger.warning(f"{filename} 데이터 없음 (필터 후) - 스킵")
                continue

            saved = save_to_db(conn, df)
            total_saved += saved
            logger.info(f"{filename} → {saved:,}행 저장")

        except Exception as e:
            logger.error(f"{filename} 처리 실패: {e}")
            failed.append(filename)
            continue

    conn.close()

    elapsed = datetime.now() - start_time
    logger.info("===== 완료 =====")
    logger.info(f"총 저장: {total_saved:,}행")
    logger.info(f"실패: {len(failed)}개 {failed}")
    logger.info(f"소요 시간: {elapsed}")


if __name__ == "__main__":
    main()