"""
collector_batch.py
==================
장 마감 후 배치 수집 (연중 매일 평일)
- 매일 16:40 crontab으로 실행
- 350종목 1분봉/5분봉 수집 → PostgreSQL 저장
- 25개 섹터 일봉 OHLCV 수집 → PostgreSQL 저장
- 실시간 수집 종목은 ON CONFLICT DO NOTHING으로 자동 스킵

※ 실전계좌 API 키 사용 (분봉 조회는 실전계좌만 가능)

crontab 설정:
40 16 * * 1-5 /home/user/miniconda3/envs/kis_collector/bin/python /home/user/collector_batch.py >> /home/user/batch.log 2>&1

패키지 설치:
pip install requests pandas psycopg2-binary openpyxl
"""

import os
import time
import requests
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime, date
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("collector_batch.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================
# 설정값
# ============================================================
# 실전계좌 API 키 (분봉 조회 필수)
APP_KEY    = os.environ.get("KIS_APP_KEY", "your_real_app_key")
APP_SECRET = os.environ.get("KIS_APP_SECRET", "your_real_app_secret")

# 실전계좌 URL (분봉 조회는 실전계좌만 가능)
BASE_URL = "https://openapi.koreainvestment.com:9443"

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     os.environ.get("DB_PORT", 5432),
    "dbname":   os.environ.get("DB_NAME", "stock_db"),
    "user":     os.environ.get("DB_USER", "stock_user"),
    "password": os.environ.get("DB_PASSWORD", "your_password"),
}

# 종목 CSV 파일 경로
TICKER_FILES = [
    "/home/user/KOSDAQ150_종목리스트.csv",
    "/home/user/KOSPI200_종목리스트.csv",
]
TICKER_COLUMN = "ticker"

# 섹터 CSV 파일 경로 (4자리 코드, 25개)
SECTOR_CSV = "/home/user/국장_섹터리스트.csv"

# API Rate Limit: 실전투자 초당 20건
# 안전하게 초당 18건으로 제한
API_INTERVAL = 0.056


# ============================================================
# 1. CSV에서 종목 코드 읽기
# ============================================================
def load_tickers() -> list:
    tickers = []
    for path in TICKER_FILES:
        if not os.path.exists(path):
            logger.warning(f"CSV 파일 없음: {path}")
            continue
        df = pd.read_csv(path, dtype=str)
        if TICKER_COLUMN not in df.columns:
            logger.error(f"컬럼 '{TICKER_COLUMN}' 없음: {path}")
            continue
        codes = df[TICKER_COLUMN].dropna().str.strip().str.zfill(6).tolist()
        tickers.extend(codes)
        logger.info(f"{path} → {len(codes)}종목 로드")

    tickers = list(dict.fromkeys(tickers))
    logger.info(f"전체 종목 수: {len(tickers)}개")
    return tickers


# ============================================================
# 2. CSV에서 섹터 코드 읽기 (4자리)
# ============================================================
def load_sector_codes() -> list:
    if not os.path.exists(SECTOR_CSV):
        logger.warning(f"섹터 CSV 없음: {SECTOR_CSV}")
        return []

    df = pd.read_csv(SECTOR_CSV, dtype=str)
    codes = df["sector_code"].dropna().str.strip().tolist()
    logger.info(f"섹터 코드 {len(codes)}개 로드")
    return codes


# ============================================================
# 3. 액세스 토큰 발급
# ============================================================
def get_access_token() -> str:
    url = f"{BASE_URL}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
    }
    res = requests.post(url, json=body, timeout=10)
    res.raise_for_status()
    token = res.json()["access_token"]
    logger.info("액세스 토큰 발급 완료")
    return token


# ============================================================
# 4. 당일 전체 1분봉 수집
#    - 1회에 30개 반환
#    - 15:30부터 30분씩 역순으로 09:00까지
# ============================================================
def fetch_all_1min(ticker: str, token: str) -> pd.DataFrame:
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST03010200",
    }

    all_rows = []
    time_cursor = "153000"

    while time_cursor >= "090000":
        params = {
            "FID_ETC_CLS_CODE": "",
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
            "FID_INPUT_HOUR_1": time_cursor,
            "FID_PW_DATA_INCU_YN": "N",
        }

        max_retry = 3
        for attempt in range(max_retry):
            try:
                res = requests.get(url, headers=headers, params=params, timeout=10)

                # 500 에러 = 해당 시간대 데이터 없음 → 재시도 없이 스킵
                if res.status_code == 500:
                    logger.debug(f"{ticker} {time_cursor} 데이터 없음 (500) - 스킵")
                    break

                res.raise_for_status()
                data = res.json()
                output2 = data.get("output2")

                if not output2:
                    logger.debug(f"{ticker} {time_cursor} output2 없음 - 스킵")
                    break

                all_rows.extend(output2)
                break

            except Exception as e:
                # 500은 위에서 처리됐으므로 여기는 진짜 네트워크 오류
                logger.warning(f"{ticker} {time_cursor} 네트워크 오류 (attempt {attempt+1}): {e}")
                time.sleep(2)

        # 30분씩 앞으로 이동
        h = int(time_cursor[:2])
        m = int(time_cursor[2:4])
        total_min = h * 60 + m - 30
        if total_min < 0:
            break
        h2 = total_min // 60
        m2 = total_min % 60
        time_cursor = f"{h2:02d}{m2:02d}00"

        time.sleep(API_INTERVAL)

    if not all_rows:
        logger.error(f"{ticker} 수집된 데이터 없음")
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df["datetime"] = pd.to_datetime(
        df["stck_bsop_date"] + df["stck_cntg_hour"],
        format="%Y%m%d%H%M%S"
    )
    df = df[["datetime", "stck_oprc", "stck_hgpr", "stck_lwpr", "stck_prpr", "cntg_vol"]]
    df.columns = ["datetime", "open", "high", "low", "close", "volume"]
    df = df.astype({"open": int, "high": int, "low": int, "close": int, "volume": int})
    df = df.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)

    logger.info(f"{ticker} 1분봉 수집 완료: {len(df)}개")
    return df


# ============================================================
# 5. 1분봉 → 5분봉 resample
# ============================================================
def resample_to_5min(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    df = df.set_index("datetime")
    # 15:25 봉 제외 (학습 데이터에 없음)
    df = df[~((df.index.hour == 15) & (df.index.minute == 25))]
    df_5min = df.resample("5min", closed="left", label="left").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna(subset=["open"])
    # 거래 없는 구간 제거
    df_5min = df_5min[df_5min["volume"] > 0]
    df_5min["ticker"] = ticker
    df_5min = df_5min.reset_index()
    logger.info(f"{ticker} 5분봉 변환 완료: {len(df_5min)}개")
    return df_5min


# ============================================================
# 6. 당일 섹터 일봉 OHLCV 수집 (한국투자증권 API)
#    - sector_code: 4자리 (예: "0005")
#    - tr_id: FHKUP03500100
# ============================================================
def fetch_sector_ohlcv(sector_code: str, today_str: str, token: str) -> dict | None:
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-index-price"
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKUP03500100",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD": sector_code,
        "FID_INPUT_DATE_1": today_str,
        "FID_INPUT_DATE_2": today_str,
        "FID_PERIOD_DIV_CODE": "D",
    }

    max_retry = 3
    for attempt in range(max_retry):
        try:
            res = requests.get(url, headers=headers, params=params, timeout=10)
            res.raise_for_status()
            data = res.json()

            if data.get("rt_cd") != "0":
                logger.warning(f"섹터 {sector_code} 응답 오류: {data.get('msg1')}")
                return None

            output2 = data.get("output2", [])
            if not output2:
                logger.warning(f"섹터 {sector_code} output2 없음")
                return None

            row = output2[0]
            if float(row.get("bstp_nmix_prpr", "0")) == 0.0:
                logger.warning(f"섹터 {sector_code} 데이터 없음 (0값)")
                return None

            return {
                "sector_code": sector_code,
                "trade_date":  today_str,
                "open":        float(row["bstp_nmix_oprc"]),
                "high":        float(row["bstp_nmix_hgpr"]),
                "low":         float(row["bstp_nmix_lwpr"]),
                "close":       float(row["bstp_nmix_prpr"]),
                "volume":      int(row["acml_vol"]),
            }

        except Exception as e:
            logger.error(f"섹터 {sector_code} 조회 실패 (attempt {attempt+1}): {e}")
            time.sleep(2)

    return None


# ============================================================
# 7. PostgreSQL 종목 1분봉/5분봉 저장
# ============================================================
def save_to_db(df_1min: pd.DataFrame, df_5min: pd.DataFrame, ticker: str):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        rows_1min = [
            (ticker, row["datetime"], row["open"], row["high"],
             row["low"], row["close"], row["volume"])
            for _, row in df_1min.iterrows()
        ]
        execute_values(cur, """
            INSERT INTO intraday_1min (ticker, datetime, open, high, low, close, volume)
            VALUES %s
            ON CONFLICT (ticker, datetime) DO NOTHING
        """, rows_1min)

        rows_5min = [
            (ticker, row["datetime"], row["open"], row["high"],
             row["low"], row["close"], row["volume"])
            for _, row in df_5min.iterrows()
        ]
        execute_values(cur, """
            INSERT INTO intraday_5min (ticker, datetime, open, high, low, close, volume)
            VALUES %s
            ON CONFLICT (ticker, datetime) DO UPDATE SET
                open   = EXCLUDED.open,
                high   = EXCLUDED.high,
                low    = EXCLUDED.low,
                close  = EXCLUDED.close,
                volume = EXCLUDED.volume
        """, rows_5min)

        conn.commit()
        logger.info(f"{ticker} DB 저장 완료 - 1분봉 {len(rows_1min)}개 / 5분봉 {len(rows_5min)}개")

    except Exception as e:
        conn.rollback()
        logger.error(f"{ticker} DB 저장 실패: {e}")
    finally:
        cur.close()
        conn.close()


# ============================================================
# 8. PostgreSQL 섹터 일봉 저장
# ============================================================
def save_sector_to_db(rows: list):
    if not rows:
        return
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        execute_values(cur, """
            INSERT INTO sector_daily_ohlcv
                (sector_code, trade_date, open, high, low, close, volume)
            VALUES %s
            ON CONFLICT (sector_code, trade_date) DO UPDATE SET
                open   = EXCLUDED.open,
                high   = EXCLUDED.high,
                low    = EXCLUDED.low,
                close  = EXCLUDED.close,
                volume = EXCLUDED.volume
        """, [
            (r["sector_code"], r["trade_date"], r["open"],
             r["high"], r["low"], r["close"], r["volume"])
            for r in rows
        ])
        conn.commit()
        logger.info(f"섹터 일봉 DB 저장 완료: {len(rows)}개")
    except Exception as e:
        conn.rollback()
        logger.error(f"섹터 일봉 저장 실패: {e}")
    finally:
        cur.close()
        conn.close()


# ============================================================
# 8-1. 1분봉 버퍼 초기화 (배치 완료 후 당일 데이터 삭제)
# ============================================================
def clear_1min_buffer():
    today = date.today()
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM intraday_1min WHERE DATE(datetime) = %s", (today,))
        deleted = cur.rowcount
        conn.commit()
        logger.info(f"1분봉 버퍼 초기화 완료: {deleted}개 삭제")
    except Exception as e:
        conn.rollback()
        logger.error(f"1분봉 버퍼 초기화 실패: {e}")
    finally:
        cur.close()
        conn.close()




# ============================================================
# 8-2. 당일 종목 일봉 수집 (한국투자증권 API)
#      - tr_id: FHKST03010100
#      - 수정주가 반영
# ============================================================
def fetch_daily_ohlcv(ticker: str, token: str) -> dict | None:
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    today_str = date.today().strftime("%Y%m%d")
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST03010100",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker,
        "FID_INPUT_DATE_1": today_str,
        "FID_INPUT_DATE_2": today_str,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",  # 수정주가
    }

    max_retry = 3
    for attempt in range(max_retry):
        try:
            res = requests.get(url, headers=headers, params=params, timeout=10)

            if res.status_code == 500:
                logger.debug(f"{ticker} 일봉 데이터 없음 (500) - 스킵")
                return None

            res.raise_for_status()
            data = res.json()

            if data.get("rt_cd") != "0":
                logger.warning(f"{ticker} 일봉 응답 오류: {data.get('msg1')}")
                return None

            output2 = data.get("output2")
            if not output2:
                logger.warning(f"{ticker} 일봉 output2 없음")
                return None

            row = output2[0]

            # 거래량 0이면 거래정지 → 스킵
            if int(row.get("acml_vol", "0")) == 0:
                logger.debug(f"{ticker} 일봉 거래량 0 (거래정지) - 스킵")
                return None

            return {
                "ticker":             ticker,
                "trade_date":         date.today(),
                "open_price":         float(row["stck_oprc"]),
                "high_price":         float(row["stck_hgpr"]),
                "low_price":          float(row["stck_lwpr"]),
                "close_price":        float(row["stck_clpr"]),
                "volume":             int(row["acml_vol"]),
                "turnover":           float(row["acml_tr_pbmn"]),
                "shares_outstanding": 0,  # FHKST03010100에서 미제공 → collector_daily_kis.py에서 별도 수집
            }

        except Exception as e:
            logger.warning(f"{ticker} 일봉 조회 실패 (attempt {attempt+1}): {e}")
            time.sleep(2)

    return None


# ============================================================
# 8-3. 종목 일봉 DB 저장
# ============================================================
def save_daily_to_db(rows: list):
    if not rows:
        return
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        # 테이블 없으면 생성
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
        execute_values(cur, """
            INSERT INTO price_daily
                (ticker, trade_date, open_price, high_price, low_price,
                 close_price, volume, turnover, shares_outstanding)
            VALUES %s
            ON CONFLICT (ticker, trade_date) DO UPDATE SET
                open_price         = EXCLUDED.open_price,
                high_price         = EXCLUDED.high_price,
                low_price          = EXCLUDED.low_price,
                close_price        = EXCLUDED.close_price,
                volume             = EXCLUDED.volume,
                turnover           = EXCLUDED.turnover,
                shares_outstanding = EXCLUDED.shares_outstanding
        """, [
            (r["ticker"], r["trade_date"], r["open_price"], r["high_price"],
             r["low_price"], r["close_price"], r["volume"],
             r["turnover"], r["shares_outstanding"])
            for r in rows
        ])
        conn.commit()
        logger.info(f"일봉 DB 저장 완료: {len(rows)}개 종목")
    except Exception as e:
        conn.rollback()
        logger.error(f"일봉 저장 실패: {e}")
    finally:
        cur.close()
        conn.close()
# ============================================================
# 9. PostgreSQL 테이블 초기화 및 재생성
# ============================================================
def reset_and_create_tables():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        logger.info("기존 테이블 삭제 중...")
        cur.execute("""
            DROP TABLE IF EXISTS intraday_1min;
            DROP TABLE IF EXISTS intraday_5min;
            DROP TABLE IF EXISTS sector_daily_ohlcv;
        """)

        logger.info("테이블 재생성 중...")
        cur.execute("""
            CREATE TABLE intraday_1min (
                ticker      VARCHAR(6)        NOT NULL,
                datetime    TIMESTAMP         NOT NULL,
                open        INTEGER           NOT NULL,
                high        INTEGER           NOT NULL,
                low         INTEGER           NOT NULL,
                close       INTEGER           NOT NULL,
                volume      BIGINT            NOT NULL,
                PRIMARY KEY (ticker, datetime)
            );
            CREATE TABLE intraday_5min (
                ticker      VARCHAR(6)        NOT NULL,
                datetime    TIMESTAMP         NOT NULL,
                open        INTEGER           NOT NULL,
                high        INTEGER           NOT NULL,
                low         INTEGER           NOT NULL,
                close       INTEGER           NOT NULL,
                volume      BIGINT            NOT NULL,
                PRIMARY KEY (ticker, datetime)
            );
            CREATE TABLE sector_daily_ohlcv (
                sector_code VARCHAR(10)       NOT NULL,
                trade_date  DATE              NOT NULL,
                open        DOUBLE PRECISION  NOT NULL,
                high        DOUBLE PRECISION  NOT NULL,
                low         DOUBLE PRECISION  NOT NULL,
                close       DOUBLE PRECISION  NOT NULL,
                volume      BIGINT            NOT NULL,
                PRIMARY KEY (sector_code, trade_date)
            );
        """)
        conn.commit()
        logger.info("테이블 초기화 완료")
    except Exception as e:
        conn.rollback()
        logger.error(f"테이블 초기화 실패: {e}")
    finally:
        cur.close()
        conn.close()


# ============================================================
# 메인
# ============================================================
def main():
    logger.info("===== 배치 수집 시작 =====")
    token = get_access_token()

    # DB 초기화 여부 (최초 1회 또는 수동 리셋 시에만 True로 설정)
    RESET_DB = os.environ.get("RESET_DB", "false").lower() == "true"
    if RESET_DB:
        logger.info("DB 초기화 모드 실행")
        reset_and_create_tables()
    else:
        # 테이블 없으면 생성만
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS intraday_1min (
                    ticker VARCHAR(6) NOT NULL, datetime TIMESTAMP NOT NULL,
                    open INTEGER NOT NULL, high INTEGER NOT NULL,
                    low INTEGER NOT NULL, close INTEGER NOT NULL,
                    volume BIGINT NOT NULL, PRIMARY KEY (ticker, datetime)
                );
                CREATE TABLE IF NOT EXISTS intraday_5min (
                    ticker VARCHAR(6) NOT NULL, datetime TIMESTAMP NOT NULL,
                    open INTEGER NOT NULL, high INTEGER NOT NULL,
                    low INTEGER NOT NULL, close INTEGER NOT NULL,
                    volume BIGINT NOT NULL, PRIMARY KEY (ticker, datetime)
                );
                CREATE TABLE IF NOT EXISTS sector_daily_ohlcv (
                    sector_code VARCHAR(10) NOT NULL, trade_date DATE NOT NULL,
                    open DOUBLE PRECISION NOT NULL, high DOUBLE PRECISION NOT NULL,
                    low DOUBLE PRECISION NOT NULL, close DOUBLE PRECISION NOT NULL,
                    volume BIGINT NOT NULL, PRIMARY KEY (sector_code, trade_date)
                );
            """)
            conn.commit()
        finally:
            cur.close()
            conn.close()

    today_str = date.today().strftime("%Y%m%d")

    # ── 종목 1분봉 / 5분봉 수집 ──────────────────────────────
    tickers = load_tickers()
    if not tickers:
        logger.error("수집할 종목 없음 - 스킵")
    else:
        total = len(tickers)
        for i, ticker in enumerate(tickers, 1):
            logger.info(f"--- [{i}/{total}] {ticker} 수집 시작 ---")
            df_1min = fetch_all_1min(ticker, token)
            if df_1min.empty:
                continue
            df_5min = resample_to_5min(df_1min, ticker)
            save_to_db(df_1min, df_5min, ticker)

    # ── 섹터 일봉 OHLCV 수집 ─────────────────────────────────
    sector_codes = load_sector_codes()
    if not sector_codes:
        logger.error("수집할 섹터 없음 - 스킵")
    else:
        sector_rows = []
        total_s = len(sector_codes)
        for i, code in enumerate(sector_codes, 1):
            logger.info(f"--- 섹터 [{i}/{total_s}] {code} 수집 ---")
            row = fetch_sector_ohlcv(code, today_str, token)
            if row:
                sector_rows.append(row)
            time.sleep(0.1)

        save_sector_to_db(sector_rows)

    # ── 종목 일봉 수집 ────────────────────────────────────────
    daily_rows = []
    total_d = len(tickers)
    for i, ticker in enumerate(tickers, 1):
        logger.info(f"--- 일봉 [{i}/{total_d}] {ticker} 수집 ---")
        row = fetch_daily_ohlcv(ticker, token)
        if row:
            daily_rows.append(row)
        time.sleep(API_INTERVAL)

    save_daily_to_db(daily_rows)

    # 1분봉 버퍼는 유지 (collector_realtime.py와 공유하므로 삭제하지 않음)

    logger.info("===== 배치 수집 완료 =====")


if __name__ == "__main__":
    main()