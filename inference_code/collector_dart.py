"""
collector_dart.py
=================
OpenDartReader를 이용한 종목 이벤트 수집
- 초기 적재: START_DATE ~ 오늘
- 매일 오전 8시 crontab 실행 시: 최근 7일치 수집

수집 항목:
    배당, 무상증자, 유상증자, 액면분할, 합병, 실적발표

crontab 설정:
    00 08 * * 1-5 /home/user/miniconda3/envs/kis_collector/bin/python /home/user/collector_dart.py >> /home/user/dart.log 2>&1

패키지 설치:
    pip install opendartreader psycopg2-binary pandas
"""

import os
import sys
import time
import logging
import contextlib
import psycopg2
from psycopg2.extras import execute_values
import pandas as pd
from datetime import date, timedelta

try:
    import OpenDartReader
except ImportError:
    print("pip install opendartreader")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("collector_dart.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================
# 설정값
# ============================================================
DART_API_KEY = os.environ.get("DART_API_KEY", "0bf0bdae6a7f53b26d77e9acbf4b7924a2060240")

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     os.environ.get("DB_PORT", 5432),
    "dbname":   os.environ.get("DB_NAME", "stock_db"),
    "user":     os.environ.get("DB_USER", "stock_user"),
    "password": os.environ.get("DB_PASSWORD", "your_password"),
}

TICKER_FILES = [
    "/home/user/KOSDAQ150_종목리스트.csv",
    "/home/user/KOSPI200_종목리스트.csv",
]
TICKER_COLUMN = "ticker"

# 매일 실행: 최근 7일치 수집
END_DATE   = date.today().strftime("%Y%m%d")
START_DATE = (date.today() - timedelta(days=7)).strftime("%Y%m%d")

# 이벤트 키워드 매핑
EVENT_KEYWORDS = {
    "유상증자":       "유상증자",
    "무상증자":       "무상증자",
    "배당":           "배당",
    "주식분할":       "액면분할",
    "실적":           "실적발표",
    "잠정":           "실적발표",
    "매출액또는손익": "실적발표",
    "합병":           "합병",
}


# ============================================================
# 1. 종목 로드
# ============================================================
def load_tickers() -> list:
    tickers = []
    for path in TICKER_FILES:
        if not os.path.exists(path):
            logger.warning(f"CSV 없음: {path}")
            continue
        df = pd.read_csv(path, dtype=str)
        codes = df[TICKER_COLUMN].dropna().str.strip().str.zfill(6).tolist()
        tickers.extend(codes)
    tickers = list(dict.fromkeys(tickers))
    tickers = [t for t in tickers if 'Z' not in t]
    logger.info(f"전체 종목: {len(tickers)}개")
    return tickers


# ============================================================
# 2. stdout 억제
# ============================================================
@contextlib.contextmanager
def suppress_stdout():
    with open(os.devnull, 'w') as devnull:
        old_stdout = sys.stdout
        sys.stdout = devnull
        try:
            yield
        finally:
            sys.stdout = old_stdout


# ============================================================
# 3. 종목별 공시 이벤트 수집
# ============================================================
def fetch_stock_events(dart, ticker: str) -> list:
    events = []
    try:
        with suppress_stdout():
            corp_code = dart.find_corp_code(ticker)
        if not corp_code:
            return []

        with suppress_stdout():
            reports = dart.list(corp_code, start=START_DATE, end=END_DATE, final=False)

        if reports is None or reports.empty:
            return []

        for _, row in reports.iterrows():
            report_nm = str(row["report_nm"]).replace(" ", "")
            for key, val in EVENT_KEYWORDS.items():
                if key in report_nm:
                    events.append({
                        "ticker":      ticker,
                        "event_date":  pd.to_datetime(row["rcept_dt"]).date(),
                        "event_type":  val,
                        "description": row["report_nm"],
                    })
                    break

    except Exception as e:
        logger.warning(f"{ticker} 공시 조회 실패: {e}")

    return events


# ============================================================
# 4. DB 저장
# ============================================================
def init_table(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_events (
            ticker      VARCHAR(10) NOT NULL,
            event_date  DATE        NOT NULL,
            event_type  VARCHAR(30) NOT NULL,
            description TEXT,
            PRIMARY KEY (ticker, event_date, event_type)
        );
    """)
    conn.commit()
    cur.close()


def save_events(conn, rows: list) -> int:
    if not rows:
        return 0
    cur = conn.cursor()
    try:
        data = [
            (r["ticker"], r["event_date"], r["event_type"], r["description"])
            for r in rows
        ]
        execute_values(cur, """
            INSERT INTO stock_events (ticker, event_date, event_type, description)
            VALUES %s
            ON CONFLICT (ticker, event_date, event_type) DO NOTHING
        """, data)
        conn.commit()
        return len(data)
    except Exception as e:
        conn.rollback()
        logger.error(f"stock_events 저장 실패: {e}")
        return 0
    finally:
        cur.close()


# ============================================================
# 메인
# ============================================================
def main():
    logger.info(f"===== 공시 이벤트 실시간 수집 시작 ({START_DATE} ~ {END_DATE}) =====")

    dart    = OpenDartReader(DART_API_KEY)
    tickers = load_tickers()
    conn    = psycopg2.connect(**DB_CONFIG)
    init_table(conn)

    total_saved = 0
    failed = []

    for i, ticker in enumerate(tickers, 1):
        logger.info(f"[{i}/{len(tickers)}] {ticker} 수집 중...")
        try:
            events = fetch_stock_events(dart, ticker)
            saved  = save_events(conn, events)
            total_saved += saved
            if saved > 0:
                logger.info(f"  {ticker} → {saved}개 이벤트 저장")
        except Exception as e:
            logger.error(f"{ticker} 처리 실패: {e}")
            failed.append(ticker)
        time.sleep(0.1)

    conn.close()
    logger.info(f"===== 완료: {total_saved}개 저장, 실패 {len(failed)}개 =====")


if __name__ == "__main__":
    main()