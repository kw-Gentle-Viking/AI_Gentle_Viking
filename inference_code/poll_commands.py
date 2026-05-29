"""
poll_commands.py
================
Option B: AI 서버가 백엔드의 커맨드 큐를 주기적으로 폴링

백엔드 API (routes_ai_command.py):
    GET  {BACKEND_WEBHOOK_URL}/ai/commands/pending
        → {"commands": [...]}  미처리 커맨드 목록 (조회 즉시 "delivered" 처리됨)

    커맨드 오브젝트:
        {"command": "START|STOP|ONCE", "user_id": int,
         "tickers": [...], "callback_url": str|null, ...}

    ONCE 결과 callback:
        POST {BACKEND_WEBHOOK_URL}/ai/callback  (X-API-Key 헤더)

환경변수:
    BACKEND_WEBHOOK_URL    : 백엔드 서버 베이스 URL
    GCP_BACKEND_API_KEY    : 백엔드 인증 키 (Bearer 토큰)
    AI_SERVER_API_KEY      : AI 서버 → 백엔드 push 인증 키 (X-API-Key)
    POLL_INTERVAL_SECONDS  : 폴링 주기 (기본 30초, 루프 모드)

실행:
    # 상시 루프
    python /home/user/poll_commands.py

    # crontab 단발성 (매 1분)
    * * * * 1-5 /home/user/miniconda3/envs/kis_collector/bin/python /home/user/poll_commands.py --once >> /home/user/poll_commands.log 2>&1
"""

import os
import sys
import logging
import threading
import time
import requests
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("poll_commands.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ONCE 추론·종목 관리 로직은 api_server.py와 공유
from api_server import run_once_inference, load_tickers, save_tickers

BACKEND_WEBHOOK_URL = os.environ.get("BACKEND_WEBHOOK_URL", "")
GCP_BACKEND_API_KEY = os.environ.get("GCP_BACKEND_API_KEY", "")
AI_SERVER_API_KEY   = os.environ.get("AI_SERVER_API_KEY", "")
POLL_INTERVAL       = int(os.environ.get("POLL_INTERVAL_SECONDS", "30"))


def _poll_headers() -> dict:
    h = {"Content-Type": "application/json"}
    if GCP_BACKEND_API_KEY:
        h["Authorization"] = f"Bearer {GCP_BACKEND_API_KEY}"
    return h


def fetch_pending() -> list:
    """백엔드 커맨드 큐에서 미처리 항목 조회 (조회 즉시 delivered 처리됨)"""
    resp = requests.get(
        f"{BACKEND_WEBHOOK_URL}/ai/commands/pending",
        headers=_poll_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("commands", [])


def process(cmd: dict):
    command      = cmd.get("command", "")
    user_id      = cmd.get("user_id")          # int
    tickers      = cmd.get("tickers", [])
    # callback_url 미지정 시 백엔드 고정 엔드포인트 사용
    callback_url = cmd.get("callback_url") or f"{BACKEND_WEBHOOK_URL}/ai/callback"

    if command == "START":
        data = load_tickers()
        data["users"][str(user_id)] = tickers
        all_t = sorted(set(t for ts in data["users"].values() for t in ts))
        data["all_tickers"] = all_t
        save_tickers(data)
        logger.info(f"START: user={user_id} → {tickers} / 전체: {all_t}")

    elif command == "STOP":
        data = load_tickers()
        removed = data["users"].pop(str(user_id), [])
        all_t = sorted(set(t for ts in data["users"].values() for t in ts))
        data["all_tickers"] = all_t
        save_tickers(data)
        logger.info(f"STOP: user={user_id} 제거({removed}) / 전체: {all_t}")

    elif command == "ONCE":
        job_id = (cmd.get("job_id")
                  or f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{user_id}")
        threading.Thread(
            target=run_once_inference,
            args=(str(user_id), tickers, callback_url, job_id),
            daemon=True,
        ).start()
        logger.info(f"ONCE 시작: {job_id} / tickers={tickers}")

    else:
        logger.warning(f"알 수 없는 커맨드: {command}")


def poll_once():
    if not BACKEND_WEBHOOK_URL:
        logger.error("BACKEND_WEBHOOK_URL 미설정 → 건너뜀")
        return

    try:
        commands = fetch_pending()
    except Exception as e:
        logger.error(f"폴링 실패: {e}")
        return

    if not commands:
        logger.debug("처리할 커맨드 없음")
        return

    logger.info(f"{len(commands)}개 커맨드 수신")
    for cmd in commands:
        try:
            process(cmd)
        except Exception as e:
            logger.error(f"커맨드 처리 오류: {e} / cmd={cmd}")


def main():
    if "--once" in sys.argv:
        poll_once()
    else:
        logger.info(f"폴링 루프 시작 (주기: {POLL_INTERVAL}초)")
        while True:
            poll_once()
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
