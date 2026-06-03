# gcp 연결 완료 및 전달사항

## 수정 파일: `app/routes_market.py`

`/market/ohlcv` 엔드포인트가 404로 떨어지고 있습니다.  
`routes_market.py` 코드 확인 후 서버 재시작(또는 재배포) 부탁드립니다.
(Python 버전 무관하게 `Optional[str]`로 수정해두었으나, 단순 재시작으로도 해결될 수 있습니다.)

### 변경 내용

```python
# 변경 전
from typing import Literal

class OhlcvRecord(BaseModel):
    trade_date: str | None = None
    trade_datetime: str | None = None

# 변경 후 (Optional[str]로 통일 — Python 버전 무관하게 호환)
from typing import Literal, Optional

class OhlcvRecord(BaseModel):
    trade_date: Optional[str] = None
    trade_datetime: Optional[str] = None
```

### 배포 후 확인

```bash
curl -X POST http://34.64.252.181:8000/market/ohlcv \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: dev-ai-key" \
  -d '{
    "timeframe": "1d",
    "records": [{
      "ticker": "005930",
      "trade_date": "2025-06-03",
      "open_price": 75000, "high_price": 76000,
      "low_price": 74500, "close_price": 75500,
      "volume": 1000000
    }]
  }'
```

정상 응답: `{"status": "ok", "timeframe": "1d", "received": 1}`

---

## AI 서버 쪽에서 GCP 연결을 위해 한 작업

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

`BACKEND_WEBHOOK_URL` 환경변수를 실제 GCP 주소로 변경 완료.
