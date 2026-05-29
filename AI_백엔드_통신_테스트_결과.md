# AI 서버 ↔ 백엔드 통신 테스트 결과

**테스트 일자**: 2026-05-29  
**네트워크 방식**: Option B (AI 서버 폴링)

---

## 1. 테스트 배경

백엔드 서버가 아직 GCP에 배포되지 않은 상태라 직접 연결 불가.  
백엔드 레포(`feature/auto-trade` 브랜치)의 AI 통신 관련 라우터 2개만 추출해  
테스트용 미니 서버(`backend_test_server.py`)를 로컬에 구성하고 검증.

---

## 2. 구성 파일

| 파일 | 역할 |
|---|---|
| `backend_test_server.py` | 테스트용 백엔드 미니 서버 (FastAPI, port 3000) |
| `poll_commands.py` | AI 서버 커맨드 폴링 스크립트 |
| `push_realtime_results.py` | AI 추론 결과 백엔드 push 스크립트 |
| `active_tickers.json` | AI 서버 종목 레지스트리 |

### backend_test_server.py가 구현한 엔드포인트

| 메서드 | 경로 | 역할 |
|---|---|---|
| `POST` | `/ai/commands/push` | 커맨드 큐에 START/STOP/ONCE 적재 |
| `GET`  | `/ai/commands/pending` | 미처리 커맨드 조회 (조회 즉시 delivered 처리) |
| `GET`  | `/ai/commands/history` | 커맨드 이력 조회 |
| `POST` | `/ai/realtime` | 5분 추론 결과 수신 (X-API-Key 인증) |
| `POST` | `/ai/callback` | ONCE 추론 결과 수신 (X-API-Key 인증) |
| `GET`  | `/ai/predictions/{ticker}` | 특정 종목 최신 추론 결과 조회 |
| `GET`  | `/ai/predictions` | 전체 종목 추론 결과 조회 |
| `GET`  | `/health` | 서버 상태 확인 |

---

## 3. 환경변수 설정

```bash
export BACKEND_WEBHOOK_URL="http://localhost:3000"
export AI_SERVER_API_KEY="dev-ai-key"
```

> `AI_SERVER_API_KEY`는 백엔드 코드(`routes_ai_webhook.py`)의 기본값 `dev-ai-key` 사용.  
> `GCP_BACKEND_API_KEY`는 현재 백엔드가 폴링 엔드포인트에 인증을 적용하지 않아 불필요.

---

## 4. 테스트 흐름 및 결과

### Step 1 — 테스트 서버 기동 확인

```bash
python /home/user/backend_test_server.py
curl http://localhost:3000/health
```

**응답**
```json
{"status": "ok", "queued": 0, "predictions": 0}
```
✅ 서버 정상 기동

---

### Step 2 — START 커맨드 적재 (백엔드 → 큐)

사용자가 자동매매를 시작할 때 백엔드가 큐에 적재하는 동작 시뮬레이션.

```bash
curl -X POST http://localhost:3000/ai/commands/push \
  -H "Content-Type: application/json" \
  -d '{"command":"START","user_id":1,"tickers":["005930","000660"]}'
```

**응답**
```json
{"status": "queued", "command": "START"}
```
✅ 커맨드 큐 적재 완료

---

### Step 3 — AI 서버 폴링 (poll_commands.py)

AI 서버가 30초마다 실행하는 폴링을 `--once` 플래그로 단발 실행.

```bash
python /home/user/poll_commands.py --once
```

**로그 출력**
```
2026-05-29 21:24:49 [INFO] 1개 커맨드 수신
2026-05-29 21:24:49 [INFO] START: user=1 → ['005930', '000660'] / 전체: ['000660', '005930']
```

**active_tickers.json 변경 결과**
```json
{
  "updated_at": "2026-05-29T21:24:49.631579",
  "users": {
    "1": ["005930", "000660"]
  },
  "all_tickers": ["000660", "005930"]
}
```
✅ 폴링으로 커맨드 수신 → 종목 레지스트리 자동 업데이트

---

### Step 4 — 5분 추론 결과 push (AI → 백엔드)

`inference_pipeline.py`가 추론 후 호출하는 push 스크립트 실행.

```bash
python /home/user/push_realtime_results.py
```

**로그 출력**
```
2026-05-29 21:26:26 [INFO] 백엔드 push 완료: 2건 → 200
```
✅ 추론 결과 2건 백엔드 전송 성공

---

### Step 5 — 백엔드 수신 결과 확인

```bash
curl http://localhost:3000/ai/predictions
```

**응답**
```json
{
  "000660": {
    "ticker": "000660",
    "signal": "BUY",
    "confidence": 0.9016,
    "prob_buy": 0.9016,
    "prob_hold": 0.0404,
    "prob_sell": 0.0580,
    "trade_datetime": "2026-05-29T15:20:00"
  },
  "005930": {
    "ticker": "005930",
    "signal": "BUY",
    "confidence": 0.8211,
    "prob_buy": 0.8211,
    "prob_hold": 0.0663,
    "prob_sell": 0.1126,
    "trade_datetime": "2026-05-29T15:20:00"
  }
}
```
✅ 백엔드가 추론 결과를 정상 저장 및 제공

---

## 5. 검증된 전체 통신 흐름

```
[백엔드 테스트 서버]
  POST /ai/commands/push (START + tickers)
         ↓
  command_queue에 적재
         ↓
[poll_commands.py] (30초마다 또는 --once)
  GET /ai/commands/pending
         ↓
  active_tickers.json 업데이트
         ↓
[inference_pipeline.py] (crontab 5분마다)
  inference.py → active_tickers.json 기반 추론
         ↓
  push_realtime_results.py
         ↓
  POST /ai/realtime (X-API-Key 인증)
         ↓
[백엔드 테스트 서버]
  realtime_predictions 저장
  → GET /ai/predictions/{ticker} 로 조회 가능
```

---

## 6. 운영 전환 시 변경사항

| 항목 | 현재 (테스트) | 운영 (GCP 배포 후) |
|---|---|---|
| `BACKEND_WEBHOOK_URL` | `http://localhost:3000` | GCP 서버 URL |
| `AI_SERVER_API_KEY` | `dev-ai-key` | 실제 발급 키 |
| `GCP_BACKEND_API_KEY` | 불필요 | 백엔드 인증 추가 시 설정 |
| 백엔드 서버 | `backend_test_server.py` | 실제 백엔드 (`app/main.py`) |
| poll_commands.py 실행 | 수동 `--once` | nohup 상시 실행 또는 crontab |
