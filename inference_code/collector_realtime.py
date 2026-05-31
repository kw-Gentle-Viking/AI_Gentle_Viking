"""
collector_realtime.py
=====================
장중 실시간 수집 (평일 매일)
- 매일 08:55 crontab으로 실행
- 1분마다 1분봉 수집 → DB 저장
- 5분 구간 완성 시 5분봉 생성 → DB 저장

5분봉 생성 규칙:
- 5분 구간 기준: 09:00~09:04, 09:05~09:09, ...
- 구간 마지막 봉(X:04, X:09 ...) 수집 후 생성
- 15:25 봉 제외 (학습 데이터 없음)
- 불완전 봉 허용: 1분봉이 5개 미만이어도 1개 이상이면 저장
- 장 마감 후 미완성 구간도 저장 (15:26~15:30 → 15:25 구간)

crontab:
    55 8 * * 1-5 /home/user/miniconda3/envs/kis_collector/bin/python /home/user/collector_realtime.py >> /home/user/realtime.log 2>&1
"""

import os
import json
import time
import requests
import pandas as pd
import psycopg2
from datetime import datetime, date, timedelta
import logging
from push_market_data import push_intraday

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("collector_realtime.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

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

TICKERS_FILE = "/home/user/active_tickers.json"

MARKET_OPEN      = datetime.now().replace(hour=9,  minute=0,  second=0, microsecond=0)
MARKET_CLOSE     = datetime.now().replace(hour=15, minute=30, second=0, microsecond=0)
MARKET_OPEN_STR  = "090000"
MARKET_CLOSE_STR = "153000"

# 15:25 봉 제외 (학습 데이터에 없음)
EXCLUDED_MINUTES = {(15, 25)}


def load_active_tickers() -> list:
    try:
        with open(TICKERS_FILE) as f:
            return json.load(f).get("all_tickers", [])
    except Exception:
        return []


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def get_access_token() -> str:
    res = requests.post(f"{BASE_URL}/oauth2/tokenP", json={
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
    }, timeout=10)
    res.raise_for_status()
    token = res.json()["access_token"]
    logger.info("토큰 발급 완료")
    return token


def floor_5min(dt: datetime) -> datetime:
    """datetime을 5분 단위로 내림 (09:03 → 09:00, 09:07 → 09:05)"""
    return dt.replace(second=0, microsecond=0, minute=(dt.minute // 5) * 5)


def is_excluded(dt: datetime) -> bool:
    """제외할 봉 시각인지 확인"""
    return (dt.hour, dt.minute) in EXCLUDED_MINUTES


def fetch_latest_1min(ticker: str, token: str) -> dict | None:
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
    now_str = datetime.now().strftime("%H%M%S")
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST03010200",
    }
    params = {
        "FID_ETC_CLS_CODE": "",
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker,
        "FID_INPUT_HOUR_1": now_str,
        "FID_PW_DATA_INCU_YN": "N",
    }
    for attempt in range(3):
        try:
            res = requests.get(url, headers=headers, params=params, timeout=5)
            if res.status_code == 500:
                return None
            res.raise_for_status()
            data = res.json()
            output2 = data.get("output2")
            if not output2:
                return None
            latest = output2[0]
            candle_dt = pd.to_datetime(
                latest["stck_bsop_date"] + latest["stck_cntg_hour"],
                format="%Y%m%d%H%M%S"
            )
            # 제외 봉 스킵
            if is_excluded(candle_dt):
                logger.debug(f"{ticker} 제외 봉: {candle_dt}")
                return None
            return {
                "ticker":   ticker,
                "datetime": candle_dt,
                "open":     int(latest["stck_oprc"]),
                "high":     int(latest["stck_hgpr"]),
                "low":      int(latest["stck_lwpr"]),
                "close":    int(latest["stck_prpr"]),
                "volume":   int(latest["cntg_vol"]),
            }
        except Exception as e:
            logger.warning(f"{ticker} 1분봉 조회 실패 (attempt {attempt+1}): {e}")
            time.sleep(2)
    return None


def save_1min(candle: dict) -> bool:
    conn = get_conn()
    cur  = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO intraday_1min (ticker, datetime, open, high, low, close, volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, datetime) DO NOTHING
        """, (candle["ticker"], candle["datetime"],
              candle["open"], candle["high"], candle["low"],
              candle["close"], candle["volume"]))
        inserted = cur.rowcount
        conn.commit()
        if inserted == 1:
            push_intraday([{
                "ticker": candle["ticker"],
                "trade_datetime": str(candle["datetime"]),
                "open_price": candle["open"], "high_price": candle["high"],
                "low_price": candle["low"], "close_price": candle["close"],
                "volume": candle["volume"],
            }], "1m")
        return inserted == 1
    except Exception as e:
        conn.rollback()
        logger.error(f"1분봉 저장 실패: {e}")
        return False
    finally:
        cur.close()
        conn.close()


def make_and_save_5min(ticker: str, dt_start: datetime):
    """
    dt_start 기준 5분 구간의 1분봉으로 5분봉 생성
    - 불완전 봉 허용 (1개 이상이면 저장)
    - 15:25 구간 제외
    """
    # 15:25 구간 제외
    if is_excluded(dt_start):
        return

    dt_end = dt_start + timedelta(minutes=5)

    conn = get_conn()
    cur  = conn.cursor()
    try:
        cur.execute("""
            SELECT datetime, open, high, low, close, volume
            FROM intraday_1min
            WHERE ticker = %s
              AND datetime >= %s
              AND datetime < %s
            ORDER BY datetime
        """, (ticker, dt_start, dt_end))
        rows = cur.fetchall()

        if not rows:
            logger.debug(f"{ticker} {dt_start} 구간 1분봉 없음 (불완전 종목)")
            return

        df = pd.DataFrame(rows, columns=["datetime","open","high","low","close","volume"])
        n = len(df)

        candle_5min = {
            "open":   int(df["open"].iloc[0]),
            "high":   int(df["high"].max()),
            "low":    int(df["low"].min()),
            "close":  int(df["close"].iloc[-1]),
            "volume": int(df["volume"].sum()),
        }

        cur.execute("""
            INSERT INTO intraday_5min (ticker, datetime, open, high, low, close, volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, datetime) DO NOTHING
        """, (ticker, dt_start,
              candle_5min["open"], candle_5min["high"],
              candle_5min["low"],  candle_5min["close"],
              candle_5min["volume"]))
        conn.commit()

        if cur.rowcount == 1:
            flag = "" if n == 5 else f" ⚠️불완전({n}개)"
            logger.info(f"{ticker} 5분봉: {dt_start} / 종가 {candle_5min['close']}{flag}")
            push_intraday([{
                "ticker": ticker,
                "trade_datetime": str(dt_start),
                "open_price": candle_5min["open"], "high_price": candle_5min["high"],
                "low_price": candle_5min["low"], "close_price": candle_5min["close"],
                "volume": candle_5min["volume"],
            }], "5m")

    except Exception as e:
        conn.rollback()
        logger.error(f"{ticker} 5분봉 저장 실패: {e}")
    finally:
        cur.close()
        conn.close()


def get_last_5min_time(ticker: str):
    conn = get_conn()
    cur  = conn.cursor()
    try:
        cur.execute("""
            SELECT MAX(datetime) FROM intraday_5min
            WHERE ticker = %s AND DATE(datetime) = %s
        """, (ticker, date.today()))
        return cur.fetchone()[0]
    except Exception:
        return None
    finally:
        cur.close()
        conn.close()


def flush_incomplete_5min(ticker: str):
    """
    장 마감 후 미완성 5분 구간 강제 저장
    마지막 5분봉 이후 남은 1분봉이 있으면 저장
    """
    last_5min = get_last_5min_time(ticker)
    if last_5min is None:
        return

    last_5min_dt = last_5min if isinstance(last_5min, datetime) else datetime.combine(date.today(), last_5min)
    next_5min_start = last_5min_dt + timedelta(minutes=5)

    # 남은 구간이 있으면 저장 시도
    while next_5min_start <= MARKET_CLOSE:
        make_and_save_5min(ticker, next_5min_start)
        next_5min_start += timedelta(minutes=5)


def is_market_open() -> bool:
    now = datetime.now().strftime("%H%M%S")
    return MARKET_OPEN_STR <= now <= MARKET_CLOSE_STR


def main():
    # 주말 가드
    if date.today().weekday() >= 5:
        logger.info("주말 → 실행 건너뜀")
        return

    tickers = load_active_tickers()
    if not tickers:
        logger.warning("active_tickers 없음 → 수집 종료")
        return

    token = get_access_token()
    token_issued_at = datetime.now()
    logger.info(f"===== 실시간 수집 시작 | 종목: {tickers} =====")

    while True:
        # 토큰 23시간마다 갱신
        if (datetime.now() - token_issued_at).seconds > 82800:
            token = get_access_token()
            token_issued_at = datetime.now()

        if not is_market_open():
            now_str = datetime.now().strftime("%H%M%S")
            if now_str < MARKET_OPEN_STR:
                logger.info("장 시작 대기 중...")
                time.sleep(30)
            else:
                logger.info("장 마감 → 미완성 5분봉 정리 후 종료")
                for ticker in tickers:
                    try:
                        flush_incomplete_5min(ticker)
                    except Exception as e:
                        logger.error(f"{ticker} 마감 정리 실패: {e}")
                break
            continue

        # 매 분 정각 + 5초 후 수집
        now = datetime.now()
        sleep_sec = 60 - now.second + 5
        logger.debug(f"다음 수집까지 {sleep_sec}초 대기")
        time.sleep(sleep_sec)

        for ticker in tickers:
            try:
                # 1분봉 수집
                candle = fetch_latest_1min(ticker, token)
                if candle is None:
                    # 수집 실패해도 5분봉 생성 시도 (불완전 봉 처리)
                    now2 = datetime.now()
                    if now2.minute % 5 == 4:
                        dt_start = floor_5min(now2)
                        make_and_save_5min(ticker, dt_start)
                    continue

                # 1분봉 저장
                is_new = save_1min(candle)
                if not is_new:
                    logger.debug(f"{ticker} 중복 스킵: {candle['datetime']}")
                    continue

                candle_dt = candle["datetime"].to_pydatetime()
                logger.info(f"{ticker} 1분봉: {candle_dt.strftime('%H:%M')} / 종가 {candle['close']}")

                # 5분 구간 마지막 봉(X:04, X:09 ...)이면 5분봉 생성
                if candle_dt.minute % 5 == 4:
                    dt_start = floor_5min(candle_dt)
                    make_and_save_5min(ticker, dt_start)

            except Exception as e:
                logger.error(f"{ticker} 처리 중 예외: {e}")


if __name__ == "__main__":
    main()