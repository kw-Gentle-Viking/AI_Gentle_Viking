"""
init_kis.py
===========
한국투자증권 API 초기 적재 (1회 실행)

수집 항목:
    - 수급 (개인/외국인/기관 순매수)
    - PER/PBR/시가총액/상장주식수
    - KOSPI/KOSDAQ/VKOSPI 지수 종가

수정사항:
    - lstn_stcn (상장주식수) 추가 수집 → price_daily 업데이트
    - NaN → None 변환 강화
    - market_cap ffill 로직 추가
"""

import os
import time
import logging
import numpy as np
import requests
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from datetime import date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("collector_daily_kis.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

APP_KEY    = os.environ.get("KIS_APP_KEY", "your_app_key")
APP_SECRET = os.environ.get("KIS_APP_SECRET", "your_app_secret")
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
INDEX_CODES   = {"0001": "kospi", "1001": "kosdaq", "101V1": "vkospi"}
API_INTERVAL  = 0.056
TODAY         = date.today()
TODAY_STR     = TODAY.strftime("%Y%m%d")


def nan_to_none(val):
    if val is None:
        return None
    try:
        if np.isnan(float(val)) or np.isinf(float(val)):
            return None
    except (TypeError, ValueError):
        pass
    return val


def load_tickers() -> list:
    tickers = []
    for path in TICKER_FILES:
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path, dtype=str)
        codes = df[TICKER_COLUMN].dropna().str.strip().str.zfill(6).tolist()
        tickers.extend(codes)
    tickers = list(dict.fromkeys(tickers))
    tickers = [t for t in tickers if 'Z' not in t]
    logger.info(f"전체 종목: {len(tickers)}개")
    return tickers


def get_access_token() -> str:
    res = requests.post(f"{BASE_URL}/oauth2/tokenP", json={
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
    }, timeout=10)
    res.raise_for_status()
    return res.json()["access_token"]


def fetch_investor(ticker: str, token: str) -> dict | None:
    """당일 투자자별 매매동향 (FHKST01010900)"""
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor"
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST01010900",
    }
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
    for attempt in range(3):
        try:
            res = requests.get(url, headers=headers, params=params, timeout=10)
            res.raise_for_status()
            data = res.json()
            if data.get("rt_cd") != "0":
                return None
            output = data.get("output", [])
            if not output:
                return None
            row = output[0]
            indv = int(row.get("prsn_ntby_tr_pbmn", 0) or 0) * 1_000_000
            frgn = int(row.get("frgn_ntby_tr_pbmn", 0) or 0) * 1_000_000
            orgn = int(row.get("orgn_ntby_tr_pbmn", 0) or 0) * 1_000_000
            return {
                "individual_net_amt": indv,
                "foreign_net_amt":    frgn,
                "inst_net_amt":       orgn,
            }
        except Exception as e:
            logger.warning(f"{ticker} 수급 조회 실패 (attempt {attempt+1}): {e}")
            time.sleep(2)
    return None


def fetch_price(ticker: str, token: str) -> dict | None:
    """당일 현재가 시세 (FHKST01010100)
    수집 항목: PER, PBR, 시가총액, 상장주식수
    """
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST01010100",
    }
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
    for attempt in range(3):
        try:
            res = requests.get(url, headers=headers, params=params, timeout=10)
            if res.status_code == 500:
                return None
            res.raise_for_status()
            data = res.json()
            if data.get("rt_cd") != "0":
                return None
            output = data.get("output", {})
            per        = nan_to_none(float(output.get("per", 0) or 0))
            pbr        = nan_to_none(float(output.get("pbr", 0) or 0))
            market_cap = int(output.get("hts_avls", 0) or 0) * 1_000_000
            lstn_stcn  = int(output.get("lstn_stcn", 0) or 0)
            return {
                "per":        per if per and per > 0 else None,
                "pbr":        pbr if pbr and pbr > 0 else None,
                "market_cap": market_cap if market_cap > 0 else None,
                "lstn_stcn":  lstn_stcn if lstn_stcn > 0 else None,
            }
        except Exception as e:
            logger.warning(f"{ticker} 시세 조회 실패 (attempt {attempt+1}): {e}")
            time.sleep(2)
    return None


def fetch_index_today(index_code: str, token: str) -> float | None:
    """당일 지수 종가"""
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-index-price"
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKUP03500100",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD":         index_code,
        "FID_INPUT_DATE_1":       TODAY_STR,
        "FID_INPUT_DATE_2":       TODAY_STR,
        "FID_PERIOD_DIV_CODE":    "D",
    }
    for attempt in range(3):
        try:
            res = requests.get(url, headers=headers, params=params, timeout=10)
            res.raise_for_status()
            data = res.json()
            if data.get("rt_cd") != "0":
                return None
            output2 = data.get("output2", [])
            if not output2:
                return None
            val = float(output2[0].get("bstp_nmix_prpr", 0) or 0)
            return val if val > 0 else None
        except Exception as e:
            logger.warning(f"지수 {index_code} 조회 실패 (attempt {attempt+1}): {e}")
            time.sleep(2)
    return None


def init_tables(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_valuation (
            ticker      VARCHAR(10)      NOT NULL,
            trade_date  DATE             NOT NULL,
            per         DOUBLE PRECISION,
            pbr         DOUBLE PRECISION,
            market_cap  BIGINT,
            PRIMARY KEY (ticker, trade_date)
        );
        CREATE TABLE IF NOT EXISTS investor_flow_daily (
            ticker              VARCHAR(10) NOT NULL,
            trade_date          DATE        NOT NULL,
            individual_net_amt  BIGINT,
            foreign_net_amt     BIGINT,
            inst_net_amt        BIGINT,
            market_cap          BIGINT,
            PRIMARY KEY (ticker, trade_date)
        );
        CREATE TABLE IF NOT EXISTS market_index_daily (
            index_code  VARCHAR(10)      NOT NULL,
            trade_date  DATE             NOT NULL,
            close_price DOUBLE PRECISION,
            PRIMARY KEY (index_code, trade_date)
        );
    """)
    conn.commit()
    cur.close()


def save_ticker_data(conn, rows: list) -> int:
    if not rows:
        return 0
    cur = conn.cursor()
    try:
        # 1. daily_valuation (PER/PBR/시가총액)
        val_rows = [
            (r["ticker"], TODAY, r.get("per"), r.get("pbr"), r.get("market_cap"))
            for r in rows
        ]
        execute_values(cur, """
            INSERT INTO daily_valuation (ticker, trade_date, per, pbr, market_cap)
            VALUES %s
            ON CONFLICT (ticker, trade_date) DO UPDATE SET
                per        = COALESCE(EXCLUDED.per,        daily_valuation.per),
                pbr        = COALESCE(EXCLUDED.pbr,        daily_valuation.pbr),
                market_cap = COALESCE(EXCLUDED.market_cap, daily_valuation.market_cap)
        """, val_rows)

        # 2. investor_flow_daily (수급)
        # market_cap은 daily_valuation에서 가져온 것으로 채움
        flow_rows = [
            (r["ticker"], TODAY,
             r.get("individual_net_amt", 0),
             r.get("foreign_net_amt", 0),
             r.get("inst_net_amt", 0),
             r.get("market_cap"))
            for r in rows
            if "individual_net_amt" in r
        ]
        if flow_rows:
            execute_values(cur, """
                INSERT INTO investor_flow_daily
                    (ticker, trade_date, individual_net_amt,
                     foreign_net_amt, inst_net_amt, market_cap)
                VALUES %s
                ON CONFLICT (ticker, trade_date) DO UPDATE SET
                    individual_net_amt = EXCLUDED.individual_net_amt,
                    foreign_net_amt    = EXCLUDED.foreign_net_amt,
                    inst_net_amt       = EXCLUDED.inst_net_amt,
                    market_cap         = COALESCE(EXCLUDED.market_cap, investor_flow_daily.market_cap)
            """, flow_rows)

        # 3. price_daily shares_outstanding 업데이트 (lstn_stcn)
        shares_rows = [
            (r["lstn_stcn"], r["ticker"], TODAY)
            for r in rows
            if r.get("lstn_stcn")
        ]
        for shares, ticker, trade_date in shares_rows:
            cur.execute("""
                UPDATE price_daily
                SET shares_outstanding = %s
                WHERE ticker = %s AND trade_date = %s
                  AND (shares_outstanding IS NULL OR shares_outstanding = 0)
            """, (shares, ticker, trade_date))

        conn.commit()

        # 저장 현황 확인
        cur.execute("""
            SELECT COUNT(*), COUNT(per), COUNT(pbr), COUNT(market_cap)
            FROM daily_valuation WHERE trade_date = %s
        """, (TODAY,))
        r = cur.fetchone()
        logger.info(f"daily_valuation({TODAY}): 총 {r[0]} / PER {r[1]} / PBR {r[2]} / 시총 {r[3]}")

        cur.execute("""
            SELECT COUNT(*), COUNT(individual_net_amt), COUNT(market_cap)
            FROM investor_flow_daily WHERE trade_date = %s
        """, (TODAY,))
        r = cur.fetchone()
        logger.info(f"investor_flow_daily({TODAY}): 총 {r[0]} / 수급 {r[1]} / 시총 {r[2]}")

        cur.execute("""
            SELECT COUNT(*) FROM price_daily
            WHERE trade_date = %s AND shares_outstanding > 0
        """, (TODAY,))
        r = cur.fetchone()
        logger.info(f"price_daily shares_outstanding 업데이트: {r[0]}종목")

        return len(rows)
    except Exception as e:
        conn.rollback()
        logger.error(f"종목 데이터 저장 실패: {e}")
        return 0
    finally:
        cur.close()


def save_index_data(conn, index_code: str, close_price: float):
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO market_index_daily (index_code, trade_date, close_price)
            VALUES (%s, %s, %s)
            ON CONFLICT (index_code, trade_date) DO UPDATE SET
                close_price = EXCLUDED.close_price
        """, (index_code, TODAY, close_price))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"지수 저장 실패: {e}")
    finally:
        cur.close()


def main():
    logger.info(f"===== 한투 API 일별 수집 시작 ({TODAY}) =====")
    token   = get_access_token()
    tickers = load_tickers()
    conn    = psycopg2.connect(**DB_CONFIG)
    init_tables(conn)

    # 1. 종목별 수급 + PER/PBR/시총/상장주식수
    logger.info("=== 종목별 수급/PER/PBR/시총/상장주식수 수집 ===")
    all_rows = []
    for i, ticker in enumerate(tickers, 1):
        try:
            investor = fetch_investor(ticker, token)
            price    = fetch_price(ticker, token)
            row = {"ticker": ticker}
            if investor:
                row.update(investor)
            if price:
                row.update(price)
            all_rows.append(row)
        except Exception as e:
            logger.error(f"{ticker} 실패: {e}")
        time.sleep(API_INTERVAL)
        if i % 50 == 0:
            logger.info(f"진행: {i}/{len(tickers)}")

    saved = save_ticker_data(conn, all_rows)
    logger.info(f"종목 데이터 완료: {saved:,}개")

    # 2. 지수
    logger.info("=== 지수 수집 ===")
    for code, name in INDEX_CODES.items():
        close = fetch_index_today(code, token)
        if close:
            save_index_data(conn, code, close)
            logger.info(f"지수 {code}({name}): {close}")
        else:
            logger.warning(f"지수 {code}({name}): 수집 실패")
        time.sleep(0.2)

    conn.close()
    logger.info("===== 완료 =====")


if __name__ == "__main__":
    main()