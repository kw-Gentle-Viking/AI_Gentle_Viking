import os
import time
import requests
import pandas as pd
from datetime import datetime, timedelta
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("collector.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================
# 설정값 (환경변수로 관리 권장)
# ============================================================
APP_KEY    = os.environ.get("KIS_APP_KEY", "your_app_key")
APP_SECRET = os.environ.get("KIS_APP_SECRET", "your_app_secret")
BASE_URL   = "https://openapi.koreainvestment.com:9443"

TICKERS = ["005930", "000660"]  # 수집할 종목 리스트
MARKET_OPEN  = "090000"
MARKET_CLOSE = "153000"

# ============================================================
# 1. 액세스 토큰 발급 (유효기간 1일)
# ============================================================
def get_access_token() -> str:
    url = f"{BASE_URL}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }
    res = requests.post(url, json=body)
    res.raise_for_status()
    token = res.json()["access_token"]
    logger.info("액세스 토큰 발급 완료")
    return token


# ============================================================
# 2. 1분봉 단건 조회
#    - 기준 시각(HHmmss)으로부터 최근 30개 반환
#    - 우리는 가장 최근 1개만 사용
# ============================================================
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

    max_retry = 3
    for attempt in range(max_retry):
        try:
            res = requests.get(url, headers=headers, params=params, timeout=5)
            res.raise_for_status()
            data = res.json()

            output2 = data.get("output2")
            if not output2:
                logger.warning(f"{ticker} output2 없음 (attempt {attempt+1})")
                time.sleep(1)
                continue

            # 가장 최근 봉 1개만 추출
            latest = output2[0]
            candle = {
                "ticker":    ticker,
                "datetime":  latest["stck_bsop_date"] + latest["stck_cntg_hour"],
                "open":      int(latest["stck_oprc"]),
                "high":      int(latest["stck_hgpr"]),
                "low":       int(latest["stck_lwpr"]),
                "close":     int(latest["stck_prpr"]),
                "volume":    int(latest["cntg_vol"]),
            }
            return candle

        except Exception as e:
            logger.error(f"{ticker} 조회 실패 (attempt {attempt+1}): {e}")
            time.sleep(2)

    return None


# ============================================================
# 3. 1분봉 → 5분봉 resample
# ============================================================
def resample_to_5min(df_1min: pd.DataFrame) -> pd.DataFrame:
    df = df_1min.copy()
    df.index = pd.to_datetime(df["datetime"], format="%Y%m%d%H%M%S")
    df = df[["open", "high", "low", "close", "volume"]]
    df_5min = df.resample("5min").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna()
    return df_5min


# ============================================================
# 4. 장 중 여부 체크
# ============================================================
def is_market_open() -> bool:
    now = datetime.now().strftime("%H%M%S")
    return MARKET_OPEN <= now <= MARKET_CLOSE


# ============================================================
# 5. 메인 루프 (1분마다 실행)
# ============================================================
def main():
    token = get_access_token()
    token_issued_at = datetime.now()

    # 종목별 1분봉 버퍼 (메모리)
    # 실제 운영 시 DB(PostgreSQL 등)로 교체 권장
    buffers: dict[str, list] = {ticker: [] for ticker in TICKERS}

    logger.info("수집 시작")

    while True:
        # 토큰 만료 전 갱신 (23시간마다)
        if (datetime.now() - token_issued_at).seconds > 82800:
            token = get_access_token()
            token_issued_at = datetime.now()

        if not is_market_open():
            logger.info("장 외 시간 - 대기 중")
            time.sleep(60)
            continue

        # 매 분 정각 이후 5초 대기 (봉 확정 시간 여유)
        now = datetime.now()
        wait = 60 - now.second + 5
        logger.info(f"다음 수집까지 {wait}초 대기")
        time.sleep(wait)

        # 종목별 수집
        for ticker in TICKERS:
            candle = fetch_latest_1min(ticker, token)
            if candle is None:
                logger.warning(f"{ticker} 수집 실패 - 스킵")
                continue

            buffers[ticker].append(candle)
            logger.info(f"{ticker} 수집 완료: {candle['datetime']} / 종가 {candle['close']}")

            # 5분봉 변환 (5개 쌓일 때마다)
            if len(buffers[ticker]) % 5 == 0:
                df_1min = pd.DataFrame(buffers[ticker][-5:])
                df_5min = resample_to_5min(df_1min)
                logger.info(f"{ticker} 5분봉 생성:\n{df_5min}")

                # TODO: 여기서 DB 저장 또는 추론 트리거 호출


if __name__ == "__main__":
    main()