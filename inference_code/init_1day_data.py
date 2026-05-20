"""
init_daily_ohlcv.py
===================
일봉 데이터 초기 적재 스크립트
- 2026-01-01 ~ 2026-04-15 기간 일봉 수집
- 350종목 전체
- 한국투자증권 API (FHKST03010100)
- 연속조회로 100건씩 수집

실행:
    conda activate kis_collector
    python init_daily_ohlcv.py
"""

import os
import time
import requests
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from datetime import date
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("init_daily_ohlcv.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================
# 설정값
# ============================================================
APP_KEY    = os.environ.get("KIS_APP_KEY", "your_real_app_key")
APP_SECRET = os.environ.get("KIS_APP_SECRET", "your_real_app_secret")
BASE_URL   = "https://openapi.koreainvestment.com:9443"

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

# 수집 기간
START_DATE = "20260101"
END_DATE   = "20260415"

API_INTERVAL = 0.056  # 초당 18건


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
        logger.info(f"{path} → {len(codes)}종목 로드")
    tickers = list(dict.fromkeys(tickers))
    logger.info(f"전체 종목 수: {len(tickers)}개")
    return tickers


# ============================================================
# 2. 액세스 토큰 발급
# ============================================================
def get_access_token() -> str:
    res = requests.post(f"{BASE_URL}/oauth2/tokenP", json={
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
    }, timeout=10)
    res.raise_for_status()
    token = res.json()["access_token"]
    logger.info("액세스 토큰 발급 완료")
    return token


# ============================================================
# 3. 종목별 기간 일봉 수집 (연속조회)
# ============================================================
def fetch_daily_range(ticker: str, token: str) -> list:
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST03010100",
    }

    all_rows = []
    fk100 = ""
    nk100 = ""
    tr_cont = ""

    while True:
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD":         ticker,
            "FID_INPUT_DATE_1":       START_DATE,
            "FID_INPUT_DATE_2":       END_DATE,
            "FID_PERIOD_DIV_CODE":    "D",   # 일봉
            "FID_ORG_ADJ_PRC":        "0",   # 수정주가
        }

        # 연속조회 시 헤더에 추가
        if tr_cont:
            headers["tr_cont"] = tr_cont
            params["FK100"] = fk100
            params["NK100"] = nk100

        max_retry = 3
        success = False
        for attempt in range(max_retry):
            try:
                res = requests.get(url, headers=headers, params=params, timeout=10)

                if res.status_code == 500:
                    logger.debug(f"{ticker} 일봉 데이터 없음 - 스킵")
                    return all_rows

                res.raise_for_status()
                data = res.json()

                if data.get("rt_cd") != "0":
                    logger.warning(f"{ticker} 응답 오류: {data.get('msg1')}")
                    return all_rows

                output2 = data.get("output2", [])
                if not output2:
                    return all_rows

                for row in output2:
                    # 거래량 0인 행 제외 (거래정지)
                    if int(row.get("acml_vol", "0")) == 0:
                        continue
                    all_rows.append({
                        "ticker":             ticker,
                        "trade_date":         row["stck_bsop_date"],
                        "open_price":         float(row["stck_oprc"]),
                        "high_price":         float(row["stck_hgpr"]),
                        "low_price":          float(row["stck_lwpr"]),
                        "close_price":        float(row["stck_clpr"]),
                        "volume":             int(row["acml_vol"]),
                        "turnover":           float(row["acml_tr_pbmn"]),
                        "shares_outstanding": int(row.get("lstn_stcn", 0)),
                    })

                # 연속조회 여부 확인
                tr_cont = res.headers.get("tr_cont", "")
                fk100   = data.get("output1", {}).get("fk100", "")
                nk100   = data.get("output1", {}).get("nk100", "")

                success = True
                break

            except Exception as e:
                logger.warning(f"{ticker} 조회 실패 (attempt {attempt+1}): {e}")
                time.sleep(2)

        if not success:
            break

        # 연속조회 종료 조건
        if tr_cont not in ("F", "M"):
            break

        time.sleep(API_INTERVAL)

    return all_rows


# ============================================================
# 4. 테이블 생성 및 일봉 저장
# ============================================================
def save_daily_to_db(rows: list):
    if not rows:
        return 0

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS price_daily (
                ticker             VARCHAR(10)      NOT NULL,
                trade_date         DATE             NOT NULL,
                open_price         DOUBLE PRECISION,
                high_price         DOUBLE PRECISION,
                low_price          DOUBLE PRECISION,
                close_price        DOUBLE PRECISION,
                volume             BIGINT,
                turnover           DOUBLE PRECISION,
                shares_outstanding BIGINT,
                PRIMARY KEY (ticker, trade_date)
            );
        """)

        data = [
            (r["ticker"],
             r["trade_date"],
             r["open_price"], r["high_price"],
             r["low_price"],  r["close_price"],
             r["volume"],     r["turnover"],
             r["shares_outstanding"])
            for r in rows
        ]
        execute_values(cur, """
            INSERT INTO price_daily
                (ticker, trade_date, open_price, high_price, low_price,
                 close_price, volume, turnover, shares_outstanding)
            VALUES %s
            ON CONFLICT (ticker, trade_date) DO NOTHING
        """, data)

        conn.commit()
        return len(data)
    except Exception as e:
        conn.rollback()
        logger.error(f"저장 실패: {e}")
        return 0
    finally:
        cur.close()
        conn.close()


# ============================================================
# 메인
# ============================================================
def main():
    logger.info(f"===== 일봉 초기 적재 시작 ({START_DATE} ~ {END_DATE}) =====")

    token = get_access_token()
    tickers = load_tickers()

    total_saved = 0
    failed = []

    for i, ticker in enumerate(tickers, 1):
        logger.info(f"[{i}/{len(tickers)}] {ticker} 수집 중...")
        try:
            rows = fetch_daily_range(ticker, token)
            if not rows:
                logger.warning(f"{ticker} 데이터 없음 - 스킵")
                continue

            saved = save_daily_to_db(rows)
            total_saved += saved
            logger.info(f"{ticker} 저장 완료: {saved}개")

        except Exception as e:
            logger.error(f"{ticker} 처리 실패: {e}")
            failed.append(ticker)

        time.sleep(API_INTERVAL)

    logger.info(f"===== 완료 =====")
    logger.info(f"총 저장: {total_saved:,}행")
    logger.info(f"실패 종목: {len(failed)}개 {failed}")


if __name__ == "__main__":
    main()