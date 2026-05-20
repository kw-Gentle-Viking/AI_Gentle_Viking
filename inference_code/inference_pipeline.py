"""
inference_pipeline.py
=====================
5분봉 피처 생성 + 추론 파이프라인 (5분마다 crontab)
- build_realtime_features → inference 순서 보장

장외 시간:
- build_realtime_features: 실행 안 함 (15:30 피처 유지)
- inference: 최신 realtime_features (15:30 기준) 로 추론

crontab:
    */5 * * * 1-5 /home/user/miniconda3/envs/kis_collector/bin/python /home/user/inference_pipeline.py >> /home/user/pipeline.log 2>&1
"""

import os
import sys
import logging
import subprocess
from datetime import datetime, date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("pipeline.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

PYTHON = "/home/user/miniconda3/envs/kis_collector/bin/python"
BASE   = "/home/user"

TODAY = date.today()
NOW   = datetime.now()

MARKET_OPEN  = NOW.replace(hour=9,  minute=0,  second=0, microsecond=0)
MARKET_CLOSE = NOW.replace(hour=15, minute=30, second=0, microsecond=0)


def run(script: str) -> bool:
    path = os.path.join(BASE, script)
    logger.info(f"실행: {script}")
    try:
        result = subprocess.run(
            [PYTHON, path],
            capture_output=True, text=True, timeout=240
        )
        if result.returncode != 0:
            logger.error(f"{script} 실패:\n{result.stderr[-500:]}")
            return False
        if result.stdout:
            logger.info(result.stdout[-500:])
        return True
    except subprocess.TimeoutExpired:
        logger.error(f"{script} 타임아웃 (240초)")
        return False
    except Exception as e:
        logger.error(f"{script} 예외: {e}")
        return False


def main():
    # 주말 가드
    if TODAY.weekday() >= 5:
        logger.info(f"주말({TODAY}) → 건너뜀")
        return

    is_market_hours = MARKET_OPEN <= NOW <= MARKET_CLOSE

    logger.info(f"===== 파이프라인 시작 ({NOW.strftime('%H:%M')}) "
                f"{'장중' if is_market_hours else '장외'} =====")

    # 1. 5분봉 피처 생성 (장중에만)
    if is_market_hours:
        ok = run("build_realtime_features.py")
        if not ok:
            logger.warning("피처 생성 실패 → 이전 피처로 추론 진행")

    # 2. 추론 (장외에도 최신 피처로 실행)
    run("inference.py")

    logger.info("===== 완료 =====")


if __name__ == "__main__":
    main()