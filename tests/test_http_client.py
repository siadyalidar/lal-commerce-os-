"""
test_http_client.py
---------------------
get_json_with_retry()'nin üç backoff modunun (exponential / header_or_linear /
fixed) ve sınırsız deneme (max_retries=None) davranışının doğrulanması.
Gerçek ağ çağrısı yapmıyor — requests.get mock'lanıyor.
"""

from unittest.mock import MagicMock, patch

import pytest

from http_client import get_json_with_retry


def _resp(status_code, json_data=None, headers=None):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_data or {}
    m.headers = headers or {}
    if status_code == 429:
        m.raise_for_status.side_effect = None
    else:
        m.raise_for_status.return_value = None
    return m


@patch("http_client.time.sleep", return_value=None)
@patch("http_client.requests.get")
def test_exponential_backoff_retries_then_succeeds(mock_get, mock_sleep):
    mock_get.side_effect = [_resp(429), _resp(429), _resp(200, {"ok": True})]
    result = get_json_with_retry("http://x", max_retries=5, backoff_mode="exponential", backoff_base_seconds=3)
    assert result == {"ok": True}
    assert mock_sleep.call_args_list[0].args[0] == 3    # 3 * 2**0
    assert mock_sleep.call_args_list[1].args[0] == 6    # 3 * 2**1


@patch("http_client.time.sleep", return_value=None)
@patch("http_client.requests.get")
def test_header_or_linear_uses_response_header(mock_get, mock_sleep):
    mock_get.side_effect = [_resp(429, headers={"X-RateLimit-Reset": "7"}), _resp(200, {"ok": True})]
    result = get_json_with_retry(
        "http://x", max_retries=5, backoff_mode="header_or_linear",
        retry_wait_header="X-RateLimit-Reset",
    )
    assert result == {"ok": True}
    assert mock_sleep.call_args_list[0].args[0] == 7


@patch("http_client.time.sleep", return_value=None)
@patch("http_client.requests.get")
def test_header_or_linear_falls_back_when_header_missing(mock_get, mock_sleep):
    mock_get.side_effect = [_resp(429), _resp(200, {"ok": True})]
    get_json_with_retry(
        "http://x", max_retries=5, backoff_mode="header_or_linear",
        backoff_base_seconds=2, retry_wait_header="X-RateLimit-Reset",
    )
    assert mock_sleep.call_args_list[0].args[0] == 2  # base*(attempt+1) = 2*1


@patch("http_client.time.sleep", return_value=None)
@patch("http_client.requests.get")
def test_fixed_backoff_always_same_wait(mock_get, mock_sleep):
    mock_get.side_effect = [_resp(429), _resp(429), _resp(429), _resp(200, {"ok": True})]
    get_json_with_retry("http://x", max_retries=None, backoff_mode="fixed", backoff_base_seconds=3)
    waits = [c.args[0] for c in mock_sleep.call_args_list]
    assert waits == [3, 3, 3]


@patch("http_client.time.sleep", return_value=None)
@patch("http_client.requests.get")
def test_max_retries_exhausted_raises(mock_get, mock_sleep):
    resp = _resp(429)
    resp.raise_for_status.side_effect = Exception("429 Too Many Requests")
    mock_get.side_effect = [resp, resp, resp]
    with pytest.raises(Exception, match="429"):
        get_json_with_retry("http://x", max_retries=3, backoff_mode="exponential")


@patch("http_client.time.sleep", return_value=None)
@patch("http_client.requests.get")
def test_throttle_seconds_applied_after_success(mock_get, mock_sleep):
    mock_get.side_effect = [_resp(200, {"ok": True})]
    get_json_with_retry("http://x", throttle_seconds=0.5, max_retries=1)
    mock_sleep.assert_called_once_with(0.5)
