# Hermes 標案資料整合設計

**日期**: 2026-07-16  
**狀態**: 已批准，待實作  
**相關專案**: bitwin（三站標案自動抓取系統）

---

## 1. 背景與目標

Hermes 是一個外部 AI Agent，需要方便地查看 bitwin 爬取的標案資料。本設計讓 Hermes 透過公開的 `data.json` 直接取得資料，並在 bitwin 端提供一個現成的 Python client helper，減少 Hermes 的重複實作。

## 2. 設計決策

選用「直接讀取 GitHub Pages data.json + 提供 hermes_client.py helper」方案，原因：

- 目前 `data.json` 僅 24 筆 / 13.7 KB，體積很小。
- 爬蟲已設定只保留「公告日 3 天內」標案，檔案不會無限增長。
- 不需要額外部署 API server，免費且維護簡單。
- Hermes 可自行決定資料呈現方式，符合「API 呼叫後自行處理」的需求。

## 3. 整體架構

```
┌─────────────┐      HTTPS GET       ┌─────────────────────────────┐
│   Hermes    │ ◀──────────────────▶ │  GitHub Pages               │
│  AI Agent   │   /bitwin/data.json  │  (docs/data.json)           │
└─────────────┘                      └─────────────────────────────┘
        │                                        │
        │ 可選：import hermes_client.py           │ 每天 08:00 更新
        │ 從 bitwin repo 複製/參考               │
        ▼                                        ▼
┌─────────────────────────┐            ┌─────────────────┐
│  BitWinClient           │            │  GitHub Actions │
│  - fetch_data()         │            │  scraper/main.py│
│  - search()             │            └─────────────────┘
│  - filter_by_source()   │
│  - get_stats()          │
└─────────────────────────┘
```

## 4. 資料來源

- **URL**: `https://espresso3568.github.io/bitwin/data.json`
- **更新頻率**: 每天台北時間 08:00（GitHub Actions 排程）
- **資料範圍**: 公告日在最近 3 天內的標案
- **格式**: JSON，包含 `update_time`、`total`、`sources`、`data` 四個欄位

## 5. Hermes Client 設計

新增 `hermes_client.py`，提供 `BitWinClient` 類別：

| 方法 | 說明 | 回傳 |
|------|------|------|
| `fetch_data()` | 從 GitHub Pages 載入 data.json | `dict` |
| `list_tenders(limit=None)` | 列出所有或前 N 筆標案 | `list[dict]` |
| `search(keyword)` | 搜尋標題、案號、來源 | `list[dict]` |
| `filter_by_source(source)` | 依機構篩選（工研院 / 資策會 / 中研院） | `list[dict]` |
| `filter_by_days(days)` | 依公告日篩選近 N 天 | `list[dict]` |
| `get_by_case_no(case_no)` | 以案號取得單筆詳情 | `dict \| None` |
| `get_stats()` | 取得更新時間、總數、各站統計 | `dict` |
| `to_markdown(tenders)` | 將標案列表格式化成 AI 易讀文字 | `str` |

### 5.1 使用範例

```python
from hermes_client import BitWinClient

client = BitWinClient()
client.fetch_data()

# 列出前 5 筆
print(client.to_markdown(client.list_tenders(5)))

# 搜尋 AI 相關標案
results = client.search("AI")
print(client.to_markdown(results))

# 取得統計
print(client.get_stats())
```

## 6. 錯誤處理

- 網路失敗：抛出 `BitWinAPIError`，附帶原始錯誤訊息。
- JSON 解析失敗：抛出 `BitWinDataError`。
- 空資料：各查詢方法回傳空列表，`get_stats()` 回傳零值統計。

## 7. 測試策略

新增 `test_hermes_client.py`，使用本地 mock JSON 測試以下情境：

- 正常載入資料
- 搜尋標題/案號/來源
- 依來源篩選
- 依日期篩選
- 單筆查詢
- 統計計算
- 網路錯誤處理

## 8. 未來擴展

若未來資料量顯著增加，或 Hermes 需要更複雜查詢，可改為部署 FastAPI server：

- 現有 `api_server.py` 已提供基礎端點 `/tenders`、`/search`、`/status`。
- 可擴充 `/filter`、`/tenders/{case_no}` 等端點。
- 部署目標可選 Render、Railway、Vercel 或 Fly.io。

---

## 9. 待實作項目

1. 新增 `hermes_client.py`
2. 新增 `test_hermes_client.py`
3. 更新 `requirements.txt`（若需要新套件）
4. 驗證 Hermes 可成功讀取與查詢
