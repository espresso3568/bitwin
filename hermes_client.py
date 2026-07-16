import datetime
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
            data: dict[str, Any] = response.json()
        except json.JSONDecodeError as exc:
            raise BitWinDataError(f"無法解析標案資料: {exc}") from exc

        self._data = data
        return data

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

    def filter_by_days(
        self, days: int, reference_date: datetime.datetime | None = None
    ) -> list[dict[str, Any]]:
        """依公告日篩選最近 N 天內的標案。"""
        data = self._ensure_data()
        cutoff = (reference_date or datetime.datetime.now()) - datetime.timedelta(
            days=days
        )
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

    def to_markdown(self, tenders: list[dict[str, Any]]) -> str:
        """將標案列表格式化成 AI 易讀的 Markdown 文字。"""
        if not tenders:
            return "無符合條件的標案。"

        lines = ["**標案列表**", ""]
        for t in tenders:
            title = t.get("標題", "無標題")
            source = t.get("來源", "未知")
            case_no = t.get("案號", "-")
            pub_date = t.get("公告日", "-")
            end_date = t.get("截止日", t.get("投標日", "-"))
            link = t.get("標題連結", "")

            lines.append(f"- **[{source}]** {title}")
            lines.append(
                f"  - 案號: `{case_no}` | 公告日: {pub_date} | 截止日: {end_date}"
            )
            if link:
                lines.append(f"  - 連結: {link}")
            lines.append("")

        return "\n".join(lines)

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
