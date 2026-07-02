# GCP 연결 완료

백엔드 GCP 서버(`http://34.64.252.181:8000`)와 AI 서버(연구실 서버) 간 연결 작업 완료 내용입니다.

---

## AI 서버에서 한 작업

연구실 서버는 인바운드 차단 환경이라 AI 서버가 백엔드로 먼저 연결을 거는 outbound 구조로 구현되어 있습니다.  
GCP 주소로 연결하기 위해 아래 환경변수를 설정했습니다.

**crontab** (자동 실행 시 적용):
```
BACKEND_WEBHOOK_URL=http://34.64.252.181:8000
AI_SERVER_API_KEY=dev-ai-key
```

**`~/.bashrc`** (수동 실행 시 적용):
```bash
export BACKEND_WEBHOOK_URL=http://34.64.252.181:8000
export AI_SERVER_API_KEY=dev-ai-key
```

코드 자체는 처음부터 `BACKEND_WEBHOOK_URL` 환경변수를 읽도록 작성되어 있어, 이 두 값만 실제 GCP 주소로 변경하면 연결이 완성됩니다.

---

## 연결 흐름 확인

| 방향 | 엔드포인트 | 상태 |
|------|-----------|------|
| AI서버 → 백엔드 | `POST /market/ohlcv` | ⏳ 백엔드 재배포 후 확인 필요 |
| AI서버 → 백엔드 | `POST /ai/realtime` (추론 결과) | ✅ 구현 완료 |
| 백엔드 → AI서버 | `GET /ai/commands/pending` (AI서버가 폴링) | ✅ 구현 완료 |
