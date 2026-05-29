"""
push_realtime_results.py
========================
inference_results DB의 최신 추론 결과를 백엔드 서버로 push
inference_pipeline.py에서 inference.py 직후 호출됨

전송 조건:
- 오늘 날짜의 결과 중 가장 최신 trade_datetime 기준
- BACKEND_WEBHOOK_URL 환경변수가 설정된 경우에만 전송
"""

import os
import json
import logging
import requests
import psycopg2
from datetime import date, datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("push_results.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     os.environ.get("DB_PORT", 5432),
    "dbname":   os.environ.get("DB_NAME", "stock_db"),
    "user":     os.environ.get("DB_USER", "stock_user"),
    "password": os.environ.get("DB_PASSWORD", "your_password"),
}

BACKEND_WEBHOOK_URL = os.environ.get("BACKEND_WEBHOOK_URL", "")
AI_SERVER_API_KEY   = os.environ.get("AI_SERVER_API_KEY", "")
TODAY = date.today()


def load_active_tickers() -> list:
    try:
        with open("/home/user/active_tickers.json") as f:
            return json.load(f).get("all_tickers", [])
    except Exception:
        return []


def fetch_latest_results(tickers: list) -> list:
    if not tickers:
        return []

    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()
    try:
        placeholders = ",".join(["%s"] * len(tickers))
        # 종목별 오늘 가장 최신 trade_datetime 결과만 가져옴
        cur.execute(f"""
            SELECT DISTINCT ON (ticker)
                ticker, trade_datetime, trade_date,
                pred_label, pred_str,
                prob_buy, prob_hold, prob_sell,
                model_version
            FROM inference_results
            WHERE trade_date = %s
              AND ticker IN ({placeholders})
            ORDER BY ticker, trade_datetime DESC
        """, [TODAY] + tickers)
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    return [
        {
            "ticker":         r[0],
            "trade_datetime": r[1].isoformat(),
            "trade_date":     r[2].isoformat(),
            "pred_label":     r[3],
            "pred_str":       r[4],
            "prob_buy":       float(r[5]),
            "prob_hold":      float(r[6]),
            "prob_sell":      float(r[7]),
            "model_version":  r[8],
        }
        for r in rows
    ]


def push_to_backend(results: list):
    payload = {
        "inference_at": datetime.now().isoformat(),
        "results":      results,
    }
    headers = {"Content-Type": "application/json"}
    if AI_SERVER_API_KEY:
        headers["X-API-Key"] = AI_SERVER_API_KEY

    try:
        resp = requests.post(
            f"{BACKEND_WEBHOOK_URL}/ai/realtime",
            json=payload,
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        logger.info(f"백엔드 push 완료: {len(results)}건 → {resp.status_code}")
    except Exception as e:
        logger.error(f"백엔드 push 실패: {e}")


def main():
    if not BACKEND_WEBHOOK_URL:
        logger.debug("BACKEND_WEBHOOK_URL 미설정 → push 건너뜀")
        return

    tickers = load_active_tickers()
    if not tickers:
        logger.warning("active_tickers 없음")
        return

    results = fetch_latest_results(tickers)
    if not results:
        logger.warning(f"오늘({TODAY}) inference_results 없음")
        return

    push_to_backend(results)


if __name__ == "__main__":
    main()
