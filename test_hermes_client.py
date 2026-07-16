import datetime
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


@pytest.fixture
def sample_data():
    return {
        "update_time": "2026-07-16 08:00:00",
        "total": 3,
        "sources": {"工研院": 2, "資策會": 1},
        "data": [
            {
                "來源": "工研院",
                "案號": "A001",
                "標題": "AI 晶片採購",
                "公告日": "2026-07-16",
            },
            {
                "來源": "工研院",
                "案號": "A002",
                "標題": "伺服器維護",
                "公告日": "2026-07-15",
            },
            {
                "來源": "資策會",
                "案號": "B001",
                "標題": "AI 教育訓練",
                "公告日": "2026-07-14",
            },
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
    reference_date = datetime.datetime(2026, 7, 16, 23, 59, 59)
    results = client.filter_by_days(2, reference_date=reference_date)
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
