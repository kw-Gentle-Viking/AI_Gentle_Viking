"""
push_market_data.py
===================
수집된 OHLCV 데이터를 백엔드 서버로 push

- collector_batch.py, collector_realtime.py에서 호출
- 실패 시 조용히 로그만 남기고 수집 흐름은 중단하지 않음
- 모든 push는 백그라운드 스레드에서 비동기 실행

환경변수:
    BACKEND_WEBHOOK_URL : 백엔드 서버 베이스 URL
    AI_SERVER_API_KEY   : X-API-Key 인증 헤더
"""

import os
import logging
import threading
import requests

logger = logging.getLogger(__name__)

BACKEND_WEBHOOK_URL = os.environ.get("BACKEND_WEBHOOK_URL", "")
AI_SERVER_API_KEY   = os.environ.get("AI_SERVER_API_KEY", "dev-ai-key")


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "X-API-Key": AI_SERVER_API_KEY,
    }


def _post(endpoint: str, payload: dict):
    if not BACKEND_WEBHOOK_URL:
        return
    try:
        resp = requests.post(
            f"{BACKEND_WEBHOOK_URL}{endpoint}",
            json=payload,
            headers=_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        logger.debug(f"push 완료 {endpoint} → {resp.status_code}")
    except Exception as e:
        logger.warning(f"push 실패 {endpoint}: {e}")


def push_daily(rows: list):
    """
    일봉 push
    rows: list of dict {ticker, trade_date, open_price, high_price, low_price, close_price, volume}
    """
    if not rows:
        return
    threading.Thread(
        target=_post,
        args=("/market/ohlcv", {"timeframe": "1d", "records": rows}),
        daemon=True,
    ).start()


def push_intraday(records: list, timeframe: str):
    """
    분봉 push
    timeframe: "1m" | "5m"
    records: list of dict {ticker, trade_datetime, open_price, high_price, low_price, close_price, volume}
    """
    if not records:
        return
    threading.Thread(
        target=_post,
        args=("/market/ohlcv", {"timeframe": timeframe, "records": records}),
        daemon=True,
    ).start()
