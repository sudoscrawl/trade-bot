from unittest.mock import patch

import httpx
import pytest


@pytest.fixture
def client():
    """Create a RoostooClient with mocked config."""
    with patch("bot.services.roostoo.config") as mock_config:
        mock_config.api_key = "test-key"
        mock_config.api_secret = "test-secret"

        from bot.services.roostoo import RoostooClient

        return RoostooClient()


class TestPing:
    def test_ping_returns_server_time(self, client):
        payload = {"serverTime": 1727740800000}
        mock_response = httpx.Response(
            status_code=200,
            json=payload,
            request=httpx.Request("GET", "https://mock-api.roostoo.com/v3/serverTime"),
        )

        with patch.object(client.client, "get", return_value=mock_response) as mock_get:
            result = client.ping()

        mock_get.assert_called_once_with("/v3/serverTime")
        assert result == payload

    def test_ping_raises_on_http_error(self, client):
        mock_response = httpx.Response(
            status_code=500,
            text="Internal Server Error",
            request=httpx.Request("GET", "https://mock-api.roostoo.com/v3/serverTime"),
        )

        with (
            patch.object(client.client, "get", return_value=mock_response),
            pytest.raises(httpx.HTTPStatusError),
        ):
            client.ping()
