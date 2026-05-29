"""
backend_test_server.py
======================
AI 서버 통신 테스트용 백엔드 미니 서버
routes_ai_command.py + routes_ai_webhook.py 기능만 포함

실행:
    python /home/user/backend_test_server.py
    → http://localhost:3000
"""

import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

AI_API_KEY = os.getenv("AI_SERVER_API_KEY", "dev-ai-key")

app = FastAPI(title="Backend Test Server")

# ── 커맨드 큐 (routes_ai_command.py) ──────────────────────────────────────

command_queue: list[dict] = []


class CommandRequest(BaseModel):
    command: str
    user_id: int
    tickers: list[str] = []
    callback_url: Optional[str] = None


@app.post("/ai/commands/push")
def push_command(payload: CommandRequest):
    cmd = {
        "command": payload.command,
        "user_id": payload.user_id,
        "tickers": payload.tickers,
        "callback_url": payload.callback_url,
        "created_at": datetime.now().isoformat(),
        "status": "pending",
    }
    command_queue.append(cmd)
    print(f"[큐 적재] {cmd}")
    return {"status": "queued", "command": payload.command}


@app.get("/ai/commands/pending")
def get_pending():
    pending = [c for c in command_queue if c["status"] == "pending"]
    for c in pending:
        c["status"] = "delivered"
    print(f"[폴링 응답] {len(pending)}개")
    return {"commands": pending}


@app.get("/ai/commands/history")
def get_history():
    return {"commands": command_queue[-50:]}


# ── 추론 결과 수신 (routes_ai_webhook.py) ─────────────────────────────────

realtime_predictions: dict[str, dict] = {}
once_results: dict[str, dict] = {}
SIGNAL_MAP = {"매수": "BUY", "관망": "HOLD", "매도": "SELL"}


class PredictionResult(BaseModel):
    ticker: str
    trade_datetime: str
    pred_label: int
    pred_str: str
    prob_buy: float
    prob_hold: float
    prob_sell: float
    model_version: str
    interpretability: Optional[dict] = None


class RealtimePayload(BaseModel):
    inference_at: str
    results: list[PredictionResult]


class OnceCallbackPayload(BaseModel):
    job_id: str
    user_id: str
    inference_at: str
    results: list[PredictionResult]


def verify_key(x_api_key: str):
    if x_api_key != AI_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


@app.post("/ai/realtime")
def receive_realtime(payload: RealtimePayload, x_api_key: str = Header(None)):
    verify_key(x_api_key)
    for r in payload.results:
        signal = SIGNAL_MAP.get(r.pred_str, "HOLD")
        confidence = max(r.prob_buy, r.prob_hold, r.prob_sell)
        realtime_predictions[r.ticker] = {
            "ticker": r.ticker, "signal": signal, "confidence": confidence,
            "prob_buy": r.prob_buy, "prob_hold": r.prob_hold, "prob_sell": r.prob_sell,
            "trade_datetime": r.trade_datetime,
        }
        print(f"[실시간 수신] {r.ticker}: {signal} (B:{r.prob_buy:.3f} H:{r.prob_hold:.3f} S:{r.prob_sell:.3f})")
    return {"status": "ok", "received": len(payload.results)}


@app.post("/ai/callback")
def receive_callback(payload: OnceCallbackPayload, x_api_key: str = Header(None)):
    verify_key(x_api_key)
    once_results[payload.job_id] = {
        "user_id": payload.user_id,
        "inference_at": payload.inference_at,
        "results": [r.model_dump() for r in payload.results],
    }
    print(f"[ONCE 수신] job_id={payload.job_id} / {len(payload.results)}건")
    return {"status": "ok", "job_id": payload.job_id}


@app.get("/ai/predictions/{ticker}")
def get_prediction(ticker: str):
    pred = realtime_predictions.get(ticker)
    if not pred:
        raise HTTPException(status_code=404, detail="추론 결과 없음")
    return pred


@app.get("/ai/predictions")
def get_all_predictions():
    return realtime_predictions


@app.get("/health")
def health():
    return {"status": "ok", "queued": len(command_queue), "predictions": len(realtime_predictions)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)
