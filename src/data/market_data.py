import pandas as pd
from datetime import datetime, timezone
from config import TRADING
from src.exchange.binance_client import BinanceClient

class MarketData:
    def __init__(self, client: BinanceClient):
        self.client = client

    def get_candles(self, symbol: str = None, interval: str = None, limit: int = 100) -> pd.DataFrame:
        symbol = symbol or TRADING["pair"]
        interval = interval or TRADING["timeframe"]
        return self.client.get_klines(symbol, interval, limit)

    def get_spread_pct(self, symbol: str = None) -> float:
        """Calculate bid-ask spread as a percentage"""
        symbol = symbol or TRADING["pair"]
        try:
            book = self.client.get_orderbook(symbol)
            best_bid = float(book["bids"][0][0])
            best_ask = float(book["asks"][0][0])
            mid = (best_bid + best_ask) / 2
            return ((best_ask - best_bid) / mid) * 100
        except Exception:
            return 999.0  # Return huge spread to block trading on error

    def get_price_with_freshness(self, symbol: str = None, max_age_seconds: int = 10) -> float:
        """
        Get current price. Raises if price data is stale.
        Stale prices can cause catastrophic order execution.
        """
        symbol = symbol or TRADING["pair"]
        price, ts = self.client.get_price(symbol)
        age = (datetime.utcnow() - ts).total_seconds()
        if age > max_age_seconds:
            raise ValueError(f"Price data is stale ({age:.1f}s old) — not trading")
        return price

    def is_liquid(self, symbol: str = None, min_spread_threshold: float = 0.1) -> bool:
        """Check if market is liquid enough to trade"""
        spread = self.get_spread_pct(symbol)
        return spread <= min_spread_threshold
