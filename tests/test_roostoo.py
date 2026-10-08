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


class TestShortEndpoints:
    def test_short_open(self, client):
        payload = {
            "Success": True,
            "ID": 412,
            "Pair": "BTC/USD",
            "OrderType": "MARKET",
            "EntryPrice": 50000.0,
            "ShortQty": 0.2,
            "Collateral": 10000.0,
            "OpenFee": 10.0,
            "Status": "OPEN",
            "CreateTimestamp": 1757980800000,
        }
        mock_response = httpx.Response(
            status_code=200,
            json=payload,
            request=httpx.Request("POST", "https://mock-api.roostoo.com/v6/short_open"),
        )
        with patch.object(client.client, "post", return_value=mock_response) as mock_post:
            result = client.short_open("BTC/USD", 10000.0)

        assert mock_post.called
        assert result["Success"] is True
        assert result["Collateral"] == 10000.0
        assert result["Pair"] == "BTC/USD"

    def test_short_close(self, client):
        payload = {
            "Success": True,
            "ClosePrice": 48000.0,
            "RealizedPNL": 400.0,
            "CloseFee": 9.6,
            "ReturnAmount": 10390.4,
            "ClosedQty": 0.2,
            "FullyClosed": True,
        }
        mock_response = httpx.Response(
            status_code=200,
            json=payload,
            request=httpx.Request("POST", "https://mock-api.roostoo.com/v6/short_close"),
        )
        with patch.object(client.client, "post", return_value=mock_response) as mock_post:
            result = client.short_close("BTC/USD")

        assert mock_post.called
        assert result["Success"] is True
        assert result["RealizedPNL"] == 400.0
        assert result["FullyClosed"] is True

    def test_get_short_positions(self, client):
        payload = {
            "Success": True,
            "Positions": [
                {
                    "ID": 412,
                    "Pair": "BTC/USD",
                    "EntryPrice": 50000.0,
                    "ShortQty": 0.2,
                    "Collateral": 10000.0,
                    "CurrentPrice": 48000.0,
                    "UnrealizedPNL": 400.0,
                    "UnrealizedPNLPct": 0.04,
                    "PositionValue": 10400.0,
                    "CreateTimestamp": 1757980800000,
                    "PositionStatus": "OPEN",
                }
            ],
        }
        mock_response = httpx.Response(
            status_code=200,
            json=payload,
            request=httpx.Request("GET", "https://mock-api.roostoo.com/v6/short_positions"),
        )
        with patch.object(client.client, "get", return_value=mock_response) as mock_get:
            result = client.get_short_positions()

        assert mock_get.called
        assert result["Success"] is True
        assert len(result["Positions"]) == 1
        assert result["Positions"][0]["UnrealizedPNL"] == 400.0
