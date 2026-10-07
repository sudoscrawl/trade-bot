"""Read-only convenience functions around the exchange client."""

from typing import Any

from bot.services.roostoo import RoostooClient


def ticker_data(client: RoostooClient) -> dict[str, dict[str, Any]]:
    return client.get_ticker().get("Data", {})


def free_balance(balance_response: dict[str, Any], asset: str) -> float:
    wallet = balance_response.get("SpotWallet") or balance_response.get("Wallet") or {}
    return float(wallet.get(asset.upper(), {}).get("Free", 0.0))


def portfolio_value_usd(
    balance_response: dict[str, Any], tickers: dict[str, dict[str, Any]]
) -> float:
    wallet = balance_response.get("SpotWallet") or balance_response.get("Wallet") or {}
    total = 0.0
    for asset, amounts in wallet.items():
        quantity = float(amounts.get("Free", 0.0)) + float(amounts.get("Lock", 0.0))
        total += (
            quantity
            if asset.upper() == "USD"
            else quantity
            * float(tickers.get(f"{asset.upper()}/USD", {}).get("LastPrice", 0.0))
        )
    return total
