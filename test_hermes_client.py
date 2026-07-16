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
            {
                "來源": "工研院",
                "案號": "A001",
                "標題": "測試標案一",
                "公告日": "2026-07-16",
            },
            {
                "來源": "資策會",
                "案號": "B001",
                "標題": "測試標案二",
                "公告日": "2026-07-16",
            },
        ],
    }
    mock_get.return_value = mock_response

    client = BitWinClient()
    data = client.fetch_data()
    assert data["total"] == 2
    assert len(data["data"]) == 2
    mock_get.assert_called_once_with(DATA_URL, timeout=30)


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
