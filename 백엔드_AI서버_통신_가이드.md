# 백엔드 서버 ↔ AI 서버 통신 가이드

---

## 1. 전체 구조

```
[사용자 앱]
    │  종목 선택 / 보고서 요청
    ▼
[백엔드 서버]
    │
    │  커맨드 (START / STOP / ONCE + tickers)
    │  ──────────────────────────────────────▶  [AI 서버]
    │                                               │
    │  ◀──────────────────────────────────────────  │  5분 추론 결과 (push)
    │  ◀──────────────────────────────────────────  │  ONCE 추론 결과 + 가중치 (callback)
    │
    │  Moirai Agent 호출 (보고서 생성, 수 분 소요)
    ▼
[사용자 앱]
```

### 커맨드 종류

| 커맨드 | 방향 | 설명 |
|---|---|---|
| `START` | 백엔드 → AI | 해당 사용자의 종목 5분 자동 추론 등록 |
| `STOP` | 백엔드 → AI | 해당 사용자의 종목 5분 자동 추론 해제 |
| `ONCE` | 백엔드 → AI | 단발성 추론 요청 (보고서용, 비동기) |

---

## 2. 커맨드 API 명세 (AI 서버 수신)

### 단일 엔드포인트

```
POST /command
Content-Type: application/json
X-API-Key: {secret_key}
```

---

### 2-1. START — 5분 자동 추론 등록

사용자가 자동매매 종목을 선택했을 때 호출.

**Request**
```json
{
  "command": "START",
  "user_id": "user_abc123",
  "tickers": ["005930", "000660", "035720"]
}
```

**Response**
```json
{
  "status": "ok",
  "command": "START",
  "user_id": "user_abc123",
  "registered": ["005930", "000660", "035720"],
  "all_active_tickers": ["005930", "000660", "035720", "000270"]
}
```

**AI 서버 동작**: 내부 종목 목록(`active_tickers.json`)에 해당 user의 종목 추가. 다음 5분 crontab부터 반영.

---

### 2-2. STOP — 5분 자동 추론 해제

사용자가 자동매매를 중단할 때 호출.

**Request**
```json
{
  "command": "STOP",
  "user_id": "user_abc123"
}
```

**Response**
```json
{
  "status": "ok",
  "command": "STOP",
  "user_id": "user_abc123",
  "removed": ["005930", "000660", "035720"],
  "all_active_tickers": ["000270"]
}
```

**AI 서버 동작**: 해당 user의 종목을 목록에서 제거. 다른 user가 동일 종목을 갖고 있으면 유지.

---

### 2-3. ONCE — 단발성 추론 요청 (보고서용, 비동기)

**Request**
```json
{
  "command": "ONCE",
  "user_id": "user_abc123",
  "tickers": ["005930", "000660"],
  "callback_url": "https://backend.example.com/ai-callback/report"
}
```

**Response (즉시 반환)**
```json
{
  "status": "accepted",
  "job_id": "report_20260522_100215_user_abc123",
  "estimated_seconds": 5
}
```

**AI 서버 동작**: 추론을 비동기로 실행. 완료 시 `callback_url`로 결과 POST 전송.

---

## 3. AI 서버 → 백엔드 전송

### 3-1. 5분 자동 추론 결과 (push)

매 5분 추론 완료 후 AI 서버가 백엔드로 전송.

```
POST {BACKEND_WEBHOOK_URL}/ai/realtime
```

**Body**
```json
{
  "inference_at": "2026-05-22T10:00:04",
  "results": [
    {
      "ticker": "005930",
      "trade_datetime": "2026-05-22T09:55:00",
      "pred_label": 0,
      "pred_str": "매수",
      "prob_buy":  0.813,
      "prob_hold": 0.068,
      "prob_sell": 0.119,
      "model_version": "best_model_state_dict.pt"
    },
    {
      "ticker": "000660",
      "trade_datetime": "2026-05-22T09:55:00",
      "pred_label": 2,
      "pred_str": "매도",
      "prob_buy":  0.182,
      "prob_hold": 0.224,
      "prob_sell": 0.594,
      "model_version": "best_model_state_dict.pt"
    }
  ]
}
```

> ticker → user_id 매핑은 백엔드가 처리. AI 서버는 ticker 기준으로만 전송.

---

### 3-2. ONCE 추론 결과 (비동기 callback)

추론 완료 후 요청 시 전달받은 `callback_url`로 전송.

```
POST {callback_url}
```

**Body**
```json
{
  "job_id": "report_20260522_100215_user_abc123",
  "user_id": "user_abc123",
  "inference_at": "2026-05-22T10:02:17",
  "results": [
    {
      "ticker": "005930",
      "trade_datetime": "2026-05-22T10:00:00",
      "pred_label": 0,
      "pred_str": "매수",
      "prob_buy":  0.813,
      "prob_hold": 0.068,
      "prob_sell": 0.119,
      "interpretability": {
        "top_features": {
          "unknown_past": [
            {
              "rank": 1,
              "feature": "rsi_14",
              "importance": 0.094,
              "current_value": 62.3,
              "description": "RSI(14)"
            },
            {
              "rank": 2,
              "feature": "log_ret",
              "importance": 0.081,
              "current_value": 0.0023,
              "description": "직전 봉 로그 수익률"
            },
            {
              "rank": 3,
              "feature": "disparity_20",
              "importance": 0.071,
              "current_value": 1.023,
              "description": "20봉 이동평균 이격도"
            },
            {
              "rank": 4,
              "feature": "kospi_ret",
              "importance": 0.062,
              "current_value": -0.0031,
              "description": "KOSPI 당일 등락률"
            },
            {
              "rank": 5,
              "feature": "prop_foreign",
              "importance": 0.058,
              "current_value": 0.0012,
              "description": "외국인 순매수 비율"
            }
          ],
          "known_future": [
            {"feature": "time_progress",  "importance": 0.412, "current_value": 0.179, "description": "장 진행률"},
            {"feature": "is_bok",         "importance": 0.201, "current_value": 0,     "description": "금통위 여부"},
            {"feature": "is_fomc",        "importance": 0.187, "current_value": 0,     "description": "FOMC 여부"},
            {"feature": "is_witching_kr", "importance": 0.112, "current_value": 0,     "description": "한국 만기일"},
            {"feature": "is_witching_us", "importance": 0.088, "current_value": 0,     "description": "미국 만기일"}
          ],
          "static": [
            {"feature": "sector_id", "importance": 0.631, "description": "섹터 (반도체)"},
            {"feature": "market_id", "importance": 0.369, "description": "시장 (KOSPI)"}
          ]
        },
        "attention_peak": [
          {"minutes_ago": 5,  "attention": 0.142},
          {"minutes_ago": 15, "attention": 0.108},
          {"minutes_ago": 30, "attention": 0.087},
          {"minutes_ago": 60, "attention": 0.063},
          {"minutes_ago": 120, "attention": 0.041}
        ]
      }
    }
  ]
}
```

---

## 4. 비동기 보고서 처리 흐름 (ONCE + Moirai Agent)

```
백엔드                          AI 서버                    Moirai Agent
  │                               │                              │
  │── POST /command (ONCE) ──────▶│                              │
  │◀─ {job_id, status: accepted} ─│                              │
  │                               │  TFT 추론 + 가중치 계산       │
  │                               │  (수 초 소요)                 │
  │◀─── callback_url 로 POST ─────│                              │
  │     {job_id, results,          │                              │
  │      interpretability}         │                              │
  │                               │                              │
  │── Moirai Agent 호출 ──────────────────────────────────────▶ │
  │   (추론결과 + 가중치 전달)                                     │
  │                               │                   보고서 생성 │
  │                               │                   (수 분 소요)│
  │◀── 보고서 반환 ───────────────────────────────────────────── │
  │                               │                              │
  │── 사용자에게 보고서 제공                                        │
```

---

## 5. 해석 가능성 데이터 상세

tft-torch `model(batch)` 출력에서 추출.

| 출력 키 | shape | 가공 방식 | 전송 내용 |
|---|---|---|---|
| `historical_selection_weights` | `[B, 60, 55]` | 60봉 평균 → per-feature score | top 5 unknown_past (importance + current_value) |
| `future_selection_weights` | `[B, 1, 5]` | squeeze | known_future 5개 전체 |
| `static_weights` | `[B, 2]` | 그대로 | sector_id, market_id 2개 전체 |
| `attention_scores` | `[B, 1, 61]` | top-K 추출 | 최근 5개 peak 시점 (minutes_ago) |

### top_features 필터링 기준

```python
# unknown_past: importance 상위 5개
weights = historical_selection_weights.mean(dim=1)  # [B, 55]
top5_idx = weights[0].argsort(descending=True)[:5]

# attention_peak: attention 상위 5개 봉 (minutes_ago 변환)
attn = attention_scores[0, 0, :60]  # encoder 60봉만
top5_attn_idx = attn.argsort(descending=True)[:5]
minutes_ago = [(60 - idx) * 5 for idx in top5_attn_idx]
```

---

## 6. AI 서버 내부 종목 관리

현재 `REALTIME_TICKERS = ["005930", "000660"]` 하드코딩 → 동적 관리로 변경 필요.

**`/home/user/active_tickers.json`**
```json
{
  "updated_at": "2026-05-22T10:00:00",
  "users": {
    "user_abc123": ["005930", "000660"],
    "user_xyz789": ["005930", "035720", "000270"]
  },
  "all_tickers": ["005930", "000660", "035720", "000270"]
}
```

5분 crontab 실행 시 `all_tickers`로 추론 → 결과를 백엔드에 push.

---

## 7. 네트워크 구성

### 현재 확정 사항

| 항목 | 값 |
|---|---|
| 백엔드 서버 위치 | GCP (Google Cloud Platform) |
| AI 서버 위치 | 로컬 서버 (on-premise) |

### 핵심 문제 — 방향별 통신 가능 여부

```
[AI 서버, 로컬]  ──────────────────────▶  [백엔드, GCP]
  OUTBOUND (가능)                           공개 HTTPS 엔드포인트

[백엔드, GCP]   ──────────────────────▶  [AI 서버, 로컬]
  INBOUND (❌ 기본 불가)                    로컬 서버는 공인 IP 없을 수 있음
```

**AI → 백엔드 (결과 push)**: 로컬에서 외부로 나가는 outbound라 항상 가능.

**백엔드 → AI (커맨드 전달)**: GCP에서 로컬 서버로 들어오는 inbound라 공인 IP + 포트 개방이 없으면 불가.

---

### 방안 A — AI 서버에 공인 IP 할당 (권장, 운영용)

로컬 서버에 공인 IP를 부여하고 FastAPI 포트를 개방.

```
GCP 백엔드  ──(HTTPS, 443 or 8443)──▶  AI 서버 공인 IP
```

**설정 필요 사항:**
- AI 서버에 공인 IP 또는 DDNS 설정
- 방화벽/공유기 포트포워딩 (→ AI 서버 내부 IP:8000)
- HTTPS 적용 (Let's Encrypt 등)
- GCP 쪽 방화벽 규칙에서 AI 서버 IP만 허용 (IP allowlist)

---

### 방안 B — Polling 방식 (공인 IP 없을 때)

AI 서버가 백엔드의 커맨드 큐를 주기적으로 조회.
백엔드가 AI 서버를 직접 호출하지 않음.

```
[백엔드 GCP]
  POST /commands/queue  ← 커맨드(START/STOP/ONCE)를 큐에 적재

[AI 서버, 로컬]
  매 N초마다 GET /commands/pending  → 큐에서 커맨드 꺼내서 처리
  커맨드 처리 후 결과 POST /ai/realtime or callback_url
```

**장점**: AI 서버 공인 IP 불필요, 방화벽 설정 없음.
**단점**: 커맨드 전달에 polling 주기(최대 N초) 지연 발생.

- START/STOP: 지연 허용 가능 (다음 5분 crontab 전에만 반영되면 충분)
- ONCE(보고서): 지연 허용 가능 (비동기 처리이므로)

---

### 방안 C — 개발 단계 임시 터널 (ngrok)

```bash
ngrok http 8000
# → https://xxxx.ngrok.io 로 AI 서버 임시 노출
```

백엔드에서 `https://xxxx.ngrok.io/command`로 커맨드 전달.
**운영 환경에서는 사용 불가**, 개발/테스트 단계에서만 활용.

---

### 권장 방향

| 단계 | 방안 |
|---|---|
| 개발/테스트 | **방안 C** (ngrok) 또는 **방안 B** (polling) |
| 운영 | **방안 A** (공인 IP) 또는 **방안 B** (polling, 지연 허용 시) |

---

### 인증 방식 (방안 A 기준)

```
AI 서버 → 백엔드 GCP:
  Authorization: Bearer {GCP_BACKEND_API_KEY}

백엔드 GCP → AI 서버:
  X-API-Key: {AI_SERVER_API_KEY}
  + IP allowlist (GCP 서버 IP만 허용)
```

---

## 8. 미확정 사항

| 항목 | 내용 |
|---|---|
| AI 서버 공인 IP 여부 | 방안 A vs B 결정에 필요 |
| 백엔드 GCP webhook URL | AI가 결과를 push할 엔드포인트 |
| Polling 주기 (방안 B) | N초 결정 필요 (권장: 10~30초) |
| 인증 키 관리 방식 | 환경변수 vs GCP Secret Manager |
