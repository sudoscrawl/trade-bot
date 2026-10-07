import hashlib
import hmac
import logging
import time

import httpx

from bot.config import config

logger = logging.getLogger(__name__)

BASE_URL = "https://mock-api.roostoo.com"


class RoostooClient:
    def __init__(self) -> None:
        configured_url = getattr(config, "exchange_base_url", BASE_URL)
        self.base_url = configured_url if isinstance(configured_url, str) else BASE_URL
        self.api_key = config.api_key
        self.api_secret = config.api_secret

        configured_timeout = getattr(config, "request_timeout_seconds", 10.0)
        timeout = (
            configured_timeout if isinstance(configured_timeout, (int, float)) else 10.0
        )
        self.client = httpx.Client(base_url=self.base_url, timeout=timeout)

        # Sync with the exchange clock so signed requests aren't rejected
        # when the local clock drifts beyond the 60-second tolerance.
        self._time_offset_ms: int = 0
        self._sync_clock()

    def _log_event(self, endpoint: str, success: bool, message: str) -> None:
        """Record an API event to the database, ignoring logging errors."""
        try:
            from bot.services.db_queries import DB

            DB.log_api_event(endpoint=endpoint, success=success, message=message)
        except Exception:
            logger.debug("Failed to record API event for %s", endpoint, exc_info=True)

    def _request(self, method: str, endpoint: str, **kwargs) -> httpx.Response:
        """Execute an HTTP request and log the API event to the database."""
        try:
            if method.upper() == "GET":
                response = self.client.get(endpoint, **kwargs)
            elif method.upper() == "POST":
                response = self.client.post(endpoint, **kwargs)
            else:
                response = self.client.request(method, endpoint, **kwargs)

            success = response.is_success
            msg = f"HTTP {response.status_code}"
            if success:
                try:
                    data = response.json()
                    if isinstance(data, dict) and not data.get("Success", True):
                        success = False
                        msg += f" - {data.get('ErrMsg', 'Exchange error')}"
                except (ValueError, TypeError):
                    logger.debug("Response is not JSON or cannot be parsed")
            else:
                msg += f": {response.text[:200]}"

            self._log_event(endpoint, success, msg)
            response.raise_for_status()
            return response
        except Exception as exc:
            if not isinstance(exc, httpx.HTTPStatusError):
                self._log_event(endpoint, False, str(exc)[:255])
            raise

    def _sync_clock(self) -> None:
        """Fetch the server time and compute the offset from the local clock."""
        try:
            local_before = int(time.time() * 1000)
            resp = self._request("GET", "/v3/serverTime")
            server_time = resp.json()["ServerTime"]
            local_after = int(time.time() * 1000)
            # Use the midpoint of the round-trip to estimate one-way latency.
            local_mid = (local_before + local_after) // 2
            self._time_offset_ms = server_time - local_mid
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            # If the time-sync request fails, fall back to local time.
            logger.debug("Server time sync failed, falling back to local time: %s", exc)
            self._time_offset_ms = 0

    def _generate_signature(self, params: dict) -> str:
        """Create an HMAC-SHA256 hex signature over sorted query params."""
        query_string = "&".join(f"{k}={params[k]}" for k in sorted(params.keys()))
        return hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _auth_headers(self, params: dict) -> dict:
        """Return the authentication headers required by the Roostoo API."""
        return {
            "RST-API-KEY": self.api_key,
            "MSG-SIGNATURE": self._generate_signature(params),
        }

    def _timestamp_ms(self) -> int:
        """Return the current UNIX timestamp in milliseconds, adjusted for server clock drift."""
        return int(time.time() * 1000) + self._time_offset_ms

    def ping(self) -> dict:
        """Get the server time from the Roostoo API to verify connectivity."""
        response = self._request("GET", "/v3/serverTime")
        return response.json()

    def get_exchange_info(self) -> dict:
        """Get exchange information (trading rules, symbol list, etc.)."""
        response = self._request("GET", "/v3/exchangeInfo")
        return response.json()

    def get_ticker(self, pair: str | None = None) -> dict:
        """Get price ticker, optionally filtered by trading *pair* (e.g. ``"BTC/USD"``)."""
        params: dict = {"timestamp": self._timestamp_ms()}
        if pair is not None:
            params["pair"] = pair

        response = self._request("GET", "/v3/ticker", params=params)
        return response.json()

    def get_balance(self) -> dict:
        """Get account balances for all assets."""
        params = {"timestamp": self._timestamp_ms()}
        response = self._request(
            "GET",
            "/v3/balance",
            params=params,
            headers=self._auth_headers(params),
        )
        return response.json()

    def place_order(
        self,
        pair: str,
        side: str,
        quantity: float,
        price: float | None = None,
    ) -> dict:
        """Place a MARKET or LIMIT order.

        Args:
            pair: Trading pair (e.g. ``"BNB/USD"``).
            side: ``"BUY"`` or ``"SELL"``.
            quantity: Order quantity.
            price: Limit price.  When *None* a MARKET order is placed.
        """
        payload: dict = {
            "timestamp": self._timestamp_ms(),
            "pair": pair.upper(),
            "side": side.upper(),
            "quantity": quantity,
        }

        if price is None:
            payload["type"] = "MARKET"
        else:
            payload["type"] = "LIMIT"
            payload["price"] = price

        response = self._request(
            "POST",
            "/v3/place_order",
            data=payload,
            headers=self._auth_headers(payload),
        )
        return response.json()

    def cancel_order(
        self,
        pair: str,
        order_id: int | None = None,
    ) -> dict:
        """Cancel open orders for a *pair*, optionally targeting a specific *order_id*."""
        payload: dict = {
            "timestamp": self._timestamp_ms(),
            "pair": pair,
        }
        if order_id is not None:
            payload["order_id"] = order_id

        response = self._request(
            "POST",
            "/v3/cancel_order",
            data=payload,
            headers=self._auth_headers(payload),
        )
        return response.json()

    def query_order(
        self,
        order_id: int | None = None,
        pair: str | None = None,
        pending_only: bool | None = None,
    ) -> dict:
        """Query orders with optional filters.

        Args:
            order_id: Filter by a specific order ID.
            pair: Filter by trading pair (e.g. ``"DASH/USD"``).
            pending_only: When ``True``, return only pending orders.
        """
        payload: dict = {"timestamp": self._timestamp_ms()}
        if order_id is not None:
            payload["order_id"] = order_id
        if pair is not None:
            payload["pair"] = pair
        if pending_only is not None:
            payload["pending_only"] = pending_only

        response = self._request(
            "POST",
            "/v3/query_order",
            data=payload,
            headers=self._auth_headers(payload),
        )
        return response.json()

    def get_pending_count(self) -> dict:
        """Get the number of pending orders."""
        params = {"timestamp": self._timestamp_ms()}
        response = self._request(
            "GET",
            "/v3/pending_count",
            params=params,
            headers=self._auth_headers(params),
        )
        return response.json()

    def close(self) -> None:
        self.client.close()
