"""
S&P500 종목 1분봉 OHLCV 수집기 (Alpaca Markets API)
====================================================
[사전 준비]
  conda activate <your_env>
  pip install alpaca-py pandas

[Alpaca API 제한 - 무료 플랜]
  - 히스토리컬 데이터: 1분봉 최대 5년치
  - 요청당 최대 10,000개 bar (1분봉 기준 약 16 거래일)
  - Rate limit: 200 req/min
  → 1년치 수집 시 종목당 약 23회 요청 필요 (10,000bar 청크)

[출력]
  ./ohlcv_data/<TICKER>_1min_1y.csv
"""

import time
import logging
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

# ─────────────────────────────────────────────────────
# ★ 설정 구역 (여기만 수정하세요)
# ─────────────────────────────────────────────────────

API_KEY    = "PKTC6TQB7MWLVV7DV56B7WCGZQ"     # Alpaca paper/live API key
API_SECRET = "Hms8eWpLVHohwffkbhSPsbFTvUTj4vyxg47tMCw4kbha"  # Alpaca secret key

# 수집할 종목 리스트 (원하는 S&P500 종목으로 변경)
TICKERS = [
    "NVDA",  # NVIDIA         #1 ~$4.6T
    "AAPL",  # Apple          #2 ~$4.0T
    "GOOG",  # Alphabet       #3 ~$3.8T
    "AMZN",  # Amazon         #4 ~$2.4T
    "AVGO",  # Broadcom       #5 ~$1.6T
    "META",  # Meta           #6 ~$1.6T
    "TSLA",  # Tesla          #7 ~$1.5T
    "LLY",   # Eli Lilly      #9 ~$968B
    "WMT",   # Walmart        #10 ~$899B
    "JPM",   # JP Morgan      #11 ~$895B
]

# 수집 기간 (UTC 기준)
START_DATE = datetime(2025, 4,  1, tzinfo=timezone.utc)
END_DATE   = datetime(2026, 3, 31, tzinfo=timezone.utc)

# 봉 단위: TimeFrame.Minute | TimeFrame.Hour | TimeFrame.Day
TIMEFRAME = TimeFrame(1, TimeFrameUnit.Minute)

# 출력 디렉토리
OUTPUT_DIR = Path("./ohlcv_data")

# 요청 간 딜레이 (초) - rate limit 방지
SLEEP_BETWEEN_TICKERS = 2

# ─────────────────────────────────────────────────────
# 로깅 설정
# ─────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("collect_ohlcv.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────
# 메인 수집 함수
# ─────────────────────────────────────────────────────
def fetch_bars(client: StockHistoricalDataClient, ticker: str) -> pd.DataFrame | None:
    """단일 종목의 OHLCV 전체를 가져와 DataFrame으로 반환."""
    log.info(f"[{ticker}] 수집 시작: {START_DATE.date()} ~ {END_DATE.date()}")

    request = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=TIMEFRAME,
        start=START_DATE,
        end=END_DATE,
        limit=None,          # None = 전체 페이지 자동 처리 (alpaca-py가 pagination 담당)
        feed="iex",          # 무료 플랜: "iex" | 유료 플랜: "sip" (더 정확)
        adjustment="all",    # 수정주가 적용 (split + dividend)
    )

    try:
        bars = client.get_stock_bars(request)
        df = bars.df  # MultiIndex: (symbol, timestamp)

        if df.empty:
            log.warning(f"[{ticker}] 데이터 없음")
            return None

        # 단일 종목이므로 symbol 레벨 제거
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(ticker, level="symbol")

        df.index.name = "datetime"
        df = df[["open", "high", "low", "close", "volume", "vwap"]]
        df.columns = ["Open", "High", "Low", "Close", "Volume", "VWAP"]

        log.info(f"[{ticker}] 수집 완료: {len(df):,}행 ({df.index[0]} ~ {df.index[-1]})")
        return df

    except Exception as e:
        log.error(f"[{ticker}] 수집 실패: {e}")
        return None


def save_csv(df: pd.DataFrame, ticker: str):
    """DataFrame을 CSV로 저장."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = OUTPUT_DIR / f"{ticker}_1min_1y.csv"
    df.to_csv(filename)
    size_kb = filename.stat().st_size / 1024
    log.info(f"[{ticker}] 저장 완료: {filename} ({size_kb:.1f} KB)")


# ─────────────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────────────
def main():
    if API_KEY == "YOUR_ALPACA_API_KEY":
        print("❌ API_KEY와 API_SECRET을 먼저 설정하세요!")
        return

    client = StockHistoricalDataClient(API_KEY, API_SECRET)

    success, fail = [], []

    for i, ticker in enumerate(TICKERS, 1):
        print(f"\n{'─'*50}")
        print(f"진행: {i}/{len(TICKERS)} — {ticker}")
        print(f"{'─'*50}")

        df = fetch_bars(client, ticker)

        if df is not None:
            save_csv(df, ticker)
            success.append(ticker)
        else:
            fail.append(ticker)

        if i < len(TICKERS):
            log.info(f"다음 종목까지 {SLEEP_BETWEEN_TICKERS}초 대기...")
            time.sleep(SLEEP_BETWEEN_TICKERS)

    # 최종 요약
    print(f"\n{'═'*50}")
    print(f"✅ 성공: {len(success)}개 — {success}")
    if fail:
        print(f"❌ 실패: {len(fail)}개 — {fail}")
    print(f"📁 저장 위치: {OUTPUT_DIR.resolve()}")
    print(f"{'═'*50}")


if __name__ == "__main__":
    main()