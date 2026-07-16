# Hermes 標案資料整合實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `hermes_client.py` 與對應測試，讓 Hermes AI Agent 能透過 Python helper 或直接讀取 GitHub Pages `data.json` 查詢標案。

**Architecture:** Hermes 端直接對 `https://espresso3568.github.io/bitwin/data.json` 發出 HTTPS GET，bitwin 端提供 `BitWinClient` 封裝載入、搜尋、篩選、統計與格式化輸出。所有查詢在 Hermes 端記憶體中處理，無需額外部署 API server。

**Tech Stack:** Python 3.10+, `requests`, `pytest`

## Global Constraints

- 資料來源 URL: `https://espresso3568.github.io/bitwin/data.json`
- 資料更新頻率: 每天台北時間 08:00
- 資料範圍: 公告日在最近 3 天內的標案
- 不依賴 pandas / FastAPI / MCP 等新框架，只用 `requests` 與標準函式庫
- 所有方法必須附帶型別提示
- 測試使用本地 mock JSON，不對外發出真實網路請求

---

### Task 1: Core client skeleton, fetch_data, and error classes

**Files:**
- Create: `hermes_client.py`
- Test: `test_hermes_client.py`（本 task 只寫 fetch_data 相關測試，後續擴充）

**Interfaces:**
- Produces: `BitWinClient` class with `fetch_data()` method
- Produces: `BitWinAPIError` and `BitWinDataError` exception classes
- Produces: `DATA_URL` constant

- [ ] **Step 1: Write failing tests for fetch_data and errors**

```python
import json
from unittest.mock import patch, Mock
import pytest
from hermes_client import BitWinClient, BitWinAPIError, BitWinDataError, DATA_URL


def test_data_url_is_correct():
    assert DATA_URL == "https://espresso3568.github.io/bitwin/data.json"


@patch("hermes_client.requests.get")
def test_fetch_data_success(mock_get):
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "update_time": "2026-07-16 08:00:00",
        "total": 2,
        "sources": {"工研院": 1, "資策會": 1},
        "data": [
            {"來源": "工研院", "案號": "A001", "標題": "測試標案一", "公告日": "2026-07-16"},
            {"來源": "資策會", "案號": "B001", "標題": "測試標案二", "公告日": "2026-07-16"},
        ],
    }
    mock_get.return_value = mock_response

    client = BitWinClient()
    data = client.fetch_data()
    assert data["total"] == 2
    assert len(data["data"]) == 2
    mock_get.assert_called_once_with(DATA_URL)


@patch("hermes_client.requests.get")
def test_fetch_data_raises_api_error_on_network_failure(mock_get):
    mock_get.side_effect = Exception("connection timeout")

    client = BitWinClient()
    with pytest.raises(BitWinAPIError) as exc_info:
        client.fetch_data()
    assert "connection timeout" in str(exc_info.value)


@patch("hermes_client.requests.get")
def test_fetch_data_raises_data_error_on_invalid_json(mock_get):
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.side_effect = json.JSONDecodeError("test", "", 0)
    mock_get.return_value = mock_response

    client = BitWinClient()
    with pytest.raises(BitWinDataError):
        client.fetch_data()
```

Run: `pytest test_hermes_client.py -v`
Expected: FAIL (`BitWinClient` not defined)

- [ ] **Step 2: Implement hermes_client.py core**

```python
import json
from typing import Any

import requests


DATA_URL = "https://espresso3568.github.io/bitwin/data.json"


class BitWinAPIError(Exception):
    """無法從 GitHub Pages 取得資料。"""


class BitWinDataError(Exception):
    """無法解析 GitHub Pages 回傳的 JSON。"""


class BitWinClient:
    """供 Hermes 讀取 BitWin 標案資料的輕量客戶端。"""

    def __init__(self, data_url: str = DATA_URL) -> None:
        self.data_url = data_url
        self._data: dict[str, Any] | None = None

    def fetch_data(self) -> dict[str, Any]:
        """從 GitHub Pages 載入最新的 data.json。"""
        try:
            response = requests.get(self.data_url, timeout=30)
            response.raise_for_status()
        except Exception as exc:
            raise BitWinAPIError(f"無法取得標案資料: {exc}") from exc

        try:
            self._data = response.json()
        except json.JSONDecodeError as exc:
            raise BitWinDataError(f"無法解析標案資料: {exc}") from exc

        return self._data
```

Run: `pytest test_hermes_client.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add hermes_client.py test_hermes_client.py
git commit -m "feat(hermes): add BitWinClient with fetch_data and error handling"
```

---

### Task 2: Query methods — list, search, filter by source, filter by days, single case, stats

**Files:**
- Modify: `hermes_client.py`
- Modify: `test_hermes_client.py`

**Interfaces:**
- Consumes: `BitWinClient.fetch_data()` from Task 1
- Produces: `BitWinClient.list_tenders(limit=None)` -> `list[dict[str, Any]]`
- Produces: `BitWinClient.search(keyword)` -> `list[dict[str, Any]]`
- Produces: `BitWinClient.filter_by_source(source)` -> `list[dict[str, Any]]`
- Produces: `BitWinClient.filter_by_days(days)` -> `list[dict[str, Any]]`
- Produces: `BitWinClient.get_by_case_no(case_no)` -> `dict[str, Any] | None`
- Produces: `BitWinClient.get_stats()` -> `dict[str, Any]`

- [ ] **Step 1: Write failing tests for query methods**

Append to `test_hermes_client.py`:

```python
import datetime
from unittest.mock import patch

import pytest


@pytest.fixture
def sample_data():
    return {
        "update_time": "2026-07-16 08:00:00",
        "total": 3,
        "sources": {"工研院": 2, "資策會": 1},
        "data": [
            {"來源": "工研院", "案號": "A001", "標題": "AI 晶片採購", "公告日": "2026-07-16"},
            {"來源": "工研院", "案號": "A002", "標題": "伺服器維護", "公告日": "2026-07-15"},
            {"來源": "資策會", "案號": "B001", "標題": "AI 教育訓練", "公告日": "2026-07-14"},
        ],
    }


@patch("hermes_client.requests.get")
def test_list_tenders(mock_get, sample_data):
    mock_get.return_value.json.return_value = sample_data
    mock_get.return_value.raise_for_status.return_value = None

    client = BitWinClient()
    client.fetch_data()
    assert len(client.list_tenders()) == 3
    assert len(client.list_tenders(limit=2)) == 2


@patch("hermes_client.requests.get")
def test_search(mock_get, sample_data):
    mock_get.return_value.json.return_value = sample_data
    mock_get.return_value.raise_for_status.return_value = None

    client = BitWinClient()
    client.fetch_data()
    results = client.search("AI")
    assert len(results) == 2
    assert all("AI" in (t["標題"] + t.get("案號", "")) for t in results)


@patch("hermes_client.requests.get")
def test_filter_by_source(mock_get, sample_data):
    mock_get.return_value.json.return_value = sample_data
    mock_get.return_value.raise_for_status.return_value = None

    client = BitWinClient()
    client.fetch_data()
    results = client.filter_by_source("工研院")
    assert len(results) == 2
    assert all(t["來源"] == "工研院" for t in results)


@patch("hermes_client.requests.get")
def test_filter_by_days(mock_get, sample_data):
    mock_get.return_value.json.return_value = sample_data
    mock_get.return_value.raise_for_status.return_value = None

    client = BitWinClient()
    client.fetch_data()
    results = client.filter_by_days(2)
    assert len(results) == 2


@patch("hermes_client.requests.get")
def test_get_by_case_no(mock_get, sample_data):
    mock_get.return_value.json.return_value = sample_data
    mock_get.return_value.raise_for_status.return_value = None

    client = BitWinClient()
    client.fetch_data()
    tender = client.get_by_case_no("A001")
    assert tender is not None
    assert tender["案號"] == "A001"
    assert client.get_by_case_no("NOT_EXIST") is None


@patch("hermes_client.requests.get")
def test_get_stats(mock_get, sample_data):
    mock_get.return_value.json.return_value = sample_data
    mock_get.return_value.raise_for_status.return_value = None

    client = BitWinClient()
    client.fetch_data()
    stats = client.get_stats()
    assert stats["update_time"] == "2026-07-16 08:00:00"
    assert stats["total"] == 3
    assert stats["sources"]["工研院"] == 2
```

Run: `pytest test_hermes_client.py -v`
Expected: FAIL (methods not defined)

- [ ] **Step 2: Implement query methods in hermes_client.py**

Append inside `BitWinClient`:

```python
    def _ensure_data(self) -> dict[str, Any]:
        """確保資料已載入；若未載入則自動 fetch。"""
        if self._data is None:
            self.fetch_data()
        return self._data  # type: ignore[return-value]

    def list_tenders(self, limit: int | None = None) -> list[dict[str, Any]]:
        """列出所有或前 N 筆標案。"""
        data = self._ensure_data()
        tenders = data.get("data", [])
        if limit is None:
            return tenders
        return tenders[:limit]

    def search(self, keyword: str) -> list[dict[str, Any]]:
        """搜尋標題或案號包含關鍵字的標案。"""
        data = self._ensure_data()
        keyword_lower = keyword.lower()
        return [
            t
            for t in data.get("data", [])
            if keyword_lower in str(t.get("標題", "")).lower()
            or keyword_lower in str(t.get("案號", "")).lower()
        ]

    def filter_by_source(self, source: str) -> list[dict[str, Any]]:
        """依來源機構篩選標案。"""
        data = self._ensure_data()
        return [t for t in data.get("data", []) if t.get("來源") == source]

    def filter_by_days(self, days: int) -> list[dict[str, Any]]:
        """依公告日篩選最近 N 天內的標案。"""
        data = self._ensure_data()
        cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
        results = []
        for t in data.get("data", []):
            pub_date = self._parse_date(str(t.get("公告日", "")))
            if pub_date and pub_date >= cutoff:
                results.append(t)
        return results

    def get_by_case_no(self, case_no: str) -> dict[str, Any] | None:
        """以案號取得單一標案詳情。"""
        data = self._ensure_data()
        for t in data.get("data", []):
            if t.get("案號") == case_no:
                return t
        return None

    def get_stats(self) -> dict[str, Any]:
        """取得更新時間、總數與各站統計。"""
        data = self._ensure_data()
        return {
            "update_time": data.get("update_time", "未知"),
            "total": data.get("total", 0),
            "sources": data.get("sources", {}),
        }

    @staticmethod
    def _parse_date(date_str: str) -> datetime.datetime | None:
        """嘗試解析常見日期格式。"""
        formats = ["%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"]
        for fmt in formats:
            try:
                return datetime.datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None
```

Add `import datetime` at the top of `hermes_client.py`.

Run: `pytest test_hermes_client.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add hermes_client.py test_hermes_client.py
git commit -m "feat(hermes): add tender query methods"
```

---

### Task 3: to_markdown formatting and empty-data handling

**Files:**
- Modify: `hermes_client.py`
- Modify: `test_hermes_client.py`

**Interfaces:**
- Consumes: query methods from Task 2
- Produces: `BitWinClient.to_markdown(tenders)` -> `str`

- [ ] **Step 1: Write failing tests for to_markdown**

Append to `test_hermes_client.py`:

```python
@patch("hermes_client.requests.get")
def test_to_markdown(mock_get, sample_data):
    mock_get.return_value.json.return_value = sample_data
    mock_get.return_value.raise_for_status.return_value = None

    client = BitWinClient()
    client.fetch_data()
    md = client.to_markdown(client.list_tenders(2))
    assert "AI 晶片採購" in md
    assert "A001" in md
    assert "工研院" in md


def test_to_markdown_empty_list():
    client = BitWinClient()
    md = client.to_markdown([])
    assert "無符合條件的標案" in md
```

Run: `pytest test_hermes_client.py -v`
Expected: FAIL (`to_markdown` not defined)

- [ ] **Step 2: Implement to_markdown**

Append inside `BitWinClient`:

```python
    def to_markdown(self, tenders: list[dict[str, Any]]) -> str:
        """將標案列表格式化成 AI 易讀的 Markdown 文字。"""
        if not tenders:
            return "📭 無符合條件的標案。"

        lines = ["📋 **標案列表**", ""]
        for t in tenders:
            title = t.get("標題", "無標題")
            source = t.get("來源", "未知")
            case_no = t.get("案號", "-")
            pub_date = t.get("公告日", "-")
            end_date = t.get("截止日", t.get("投標日", "-"))
            link = t.get("標題連結", "")

            lines.append(f"- **[{source}]** {title}")
            lines.append(f"  - 案號: `{case_no}` | 公告日: {pub_date} | 截止日: {end_date}")
            if link:
                lines.append(f"  - 連結: {link}")
            lines.append("")

        return "\n".join(lines)
```

Run: `pytest test_hermes_client.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add hermes_client.py test_hermes_client.py
git commit -m "feat(hermes): add to_markdown formatter"
```

---

### Task 4: Comprehensive tests and edge cases

**Files:**
- Modify: `test_hermes_client.py`

**Interfaces:**
- Consumes: all `BitWinClient` methods

- [ ] **Step 1: Add edge-case tests**

Append to `test_hermes_client.py`:

```python
@patch("hermes_client.requests.get")
def test_methods_return_empty_when_no_data(mock_get):
    mock_get.return_value.json.return_value = {"data": []}
    mock_get.return_value.raise_for_status.return_value = None

    client = BitWinClient()
    client.fetch_data()
    assert client.list_tenders() == []
    assert client.search("AI") == []
    assert client.filter_by_source("工研院") == []
    assert client.get_by_case_no("A001") is None
    stats = client.get_stats()
    assert stats["total"] == 0


@patch("hermes_client.requests.get")
def test_search_is_case_insensitive(mock_get, sample_data):
    mock_get.return_value.json.return_value = sample_data
    mock_get.return_value.raise_for_status.return_value = None

    client = BitWinClient()
    client.fetch_data()
    assert len(client.search("ai")) == 2
    assert len(client.search("A001")) == 1
```

Run: `pytest test_hermes_client.py -v`
Expected: PASS

- [ ] **Step 2: Commit**

```bash
git add test_hermes_client.py
git commit -m "test(hermes): add edge case coverage"
```

---

### Task 5: Verify real-world integration

**Files:**
- None (verification only)

**Interfaces:**
- Consumes: `BitWinClient` against real `data.json`

- [ ] **Step 1: Run a quick smoke test against real data**

Create a temporary script or run inline:

```bash
python - <<'PY'
from hermes_client import BitWinClient
client = BitWinClient()
client.fetch_data()
print(client.get_stats())
print(client.to_markdown(client.list_tenders(3)))
PY
```

Expected: prints current stats and top 3 tenders without errors.

- [ ] **Step 2: Run full test suite**

```bash
pytest test_hermes_client.py -v
```

Expected: all tests PASS.

- [ ] **Step 3: Final commit if any changes**

If the smoke test revealed issues, fix and commit. Otherwise no commit needed.

---

## Self-Review Checklist

- [x] Spec coverage: fetch_data, list, search, source filter, date filter, single case lookup, stats, to_markdown, error handling, tests — all have tasks.
- [x] No placeholders: every step contains concrete code or exact commands.
- [x] Type consistency: method signatures and field names match the spec (`filter_by_source`, `filter_by_days`, `get_by_case_no`, `get_stats`, `to_markdown`).
- [x] No external network calls in tests: all tests mock `requests.get`.
