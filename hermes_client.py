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
