"""
init_sector_data.py
===================
섹터 일봉 초기 적재 스크립트 (연속조회)
- 한국투자증권 API (FHKUP03500100)
- 1회 최대 50건 반환 → 연속조회로 전체 기간 수집
- 25개 섹터 전체

실행:
    conda activate kis_collector
    python init_sector_ohlcv.py
"""

import os
import time
import requests
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("init_sector_ohlcv.log"),
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

SECTOR_CSV = "/home/user/국장_섹터리스트.csv"

# 수집 기간
START_DATE = "20260101"  # YYYYMMDD
END_DATE   = "20260415"  # YYYYMMDD

API_INTERVAL = 0.2  # 연속조회 간격


# ============================================================
# 1. 섹터 코드 로드
# ============================================================
def load_sector_codes() -> list:
    if not os.path.exists(SECTOR_CSV):
        logger.error(f"섹터 CSV 없음: {SECTOR_CSV}")
        return []
    df = pd.read_csv(SECTOR_CSV, dtype=str)
    codes = df["sector_code"].dropna().str.strip().tolist()
    logger.info(f"섹터 코드 {len(codes)}개 로드")
    return codes


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
# 3. 섹터별 기간 일봉 연속조회 수집
#    - 1회 최대 50건 반환
#    - tr_cont = "F" or "M" 이면 연속조회
#    - tr_cont = "D" or "" 이면 종료
# ============================================================
def _fetch_sector_chunk(sector_code: str, token: str, start: str, end: str) -> list:
    """날짜 범위 1개 청크 조회"""
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-index-price"
    headers = {
        "authorization": f"Bearer {token}",
        "appkey":        APP_KEY,
        "appsecret":     APP_SECRET,
        "tr_id":         "FHKUP03500100",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD":         sector_code,
        "FID_INPUT_DATE_1":       start,
        "FID_INPUT_DATE_2":       end,
        "FID_PERIOD_DIV_CODE":    "D",
    }

    max_retry = 3
    for attempt in range(max_retry):
        try:
            res = requests.get(url, headers=headers, params=params, timeout=10)
            if res.status_code == 500:
                return []
            res.raise_for_status()
            data = res.json()
            if data.get("rt_cd") != "0":
                return []

            output2 = data.get("output2", [])
            rows = []
            for row in output2:
                close = float(row.get("bstp_nmix_prpr", "0"))
                if close == 0.0:
                    continue
                rows.append({
                    "sector_code": sector_code,
                    "trade_date":  row["stck_bsop_date"],
                    "open":        float(row["bstp_nmix_oprc"]),
                    "high":        float(row["bstp_nmix_hgpr"]),
                    "low":         float(row["bstp_nmix_lwpr"]),
                    "close":       close,
                    "volume":      int(row["acml_vol"]),
                })
            return rows

        except Exception as e:
            logger.warning(f"섹터 {sector_code} [{start}~{end}] 조회 실패 (attempt {attempt+1}): {e}")
            time.sleep(2)

    return []


def fetch_sector_range(sector_code: str, token: str) -> list:
    """날짜 범위를 45일씩 나눠서 전체 수집 (API 최대 50건 제한 대응)"""
    from datetime import datetime, timedelta

    start_dt = datetime.strptime(START_DATE, "%Y%m%d")
    end_dt   = datetime.strptime(END_DATE,   "%Y%m%d")

    all_rows = []
    chunk_days = 45  # 45거래일씩 (공휴일 감안해서 여유있게)

    current = start_dt
    while current <= end_dt:
        chunk_end = min(current + timedelta(days=chunk_days), end_dt)
        start_str = current.strftime("%Y%m%d")
        end_str   = chunk_end.strftime("%Y%m%d")

        rows = _fetch_sector_chunk(sector_code, token, start_str, end_str)
        all_rows.extend(rows)
        logger.debug(f"섹터 {sector_code} [{start_str}~{end_str}] {len(rows)}개 수집")

        current = chunk_end + timedelta(days=1)
        time.sleep(API_INTERVAL)

    # 중복 제거
    seen = set()
    unique_rows = []
    for r in all_rows:
        key = (r["sector_code"], r["trade_date"])
        if key not in seen:
            seen.add(key)
            unique_rows.append(r)

    logger.info(f"섹터 {sector_code} 수집 완료: {len(unique_rows)}개")
    return unique_rows


# ============================================================
# 4. DB 저장
# ============================================================
def save_to_db(rows: list) -> int:
    if not rows:
        return 0

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        execute_values(cur, """
            INSERT INTO sector_daily_ohlcv
                (sector_code, trade_date, open, high, low, close, volume)
            VALUES %s
            ON CONFLICT (sector_code, trade_date) DO NOTHING
        """, [
            (r["sector_code"], r["trade_date"],
             r["open"], r["high"], r["low"], r["close"], r["volume"])
            for r in rows
        ])
        conn.commit()
        return len(rows)
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
    logger.info(f"===== 섹터 일봉 초기 적재 시작 ({START_DATE} ~ {END_DATE}) =====")

    token = get_access_token()
    sector_codes = load_sector_codes()

    if not sector_codes:
        logger.error("섹터 코드 없음 - 종료")
        return

    total_saved = 0
    failed = []

    for i, code in enumerate(sector_codes, 1):
        logger.info(f"[{i}/{len(sector_codes)}] 섹터 {code} 수집 중...")
        try:
            rows = fetch_sector_range(code, token)
            if not rows:
                logger.warning(f"섹터 {code} 데이터 없음 - 스킵")
                continue

            saved = save_to_db(rows)
            total_saved += saved
            logger.info(f"섹터 {code} 저장 완료: {saved}개")

        except Exception as e:
            logger.error(f"섹터 {code} 처리 실패: {e}")
            failed.append(code)

        time.sleep(API_INTERVAL)

    logger.info("===== 완료 =====")
    logger.info(f"총 저장: {total_saved:,}행")
    logger.info(f"실패 섹터: {len(failed)}개 {failed}")


if __name__ == "__main__":
    main()