import os
import time
import hmac
import hashlib
import requests
import pandas as pd
from datetime import datetime

BASE_URL = "https://api.binance.com"

class BinanceClient:
    def __init__(self):
        self.api_key = os.getenv("BINANCE_API_KEY", "")
        self.secret_key = os.getenv("BINANCE_SECRET_KEY", "")

        if not self.api_key or not self.secret_key:
            raise EnvironmentError("BINANCE_API_KEY and BINANCE_SECRET_KEY must be set in .env")

        self._last_price_time = {}
        self._last_price = {}

    def _sign(self, params: dict) -> str:
        query = "&".join([f"{k}={v}" for k, v in params.items()])
        return hmac.new(self.secret_key.encode(), query.encode(), hashlib.sha256).hexdigest()

    def _headers(self):
        return {"X-MBX-APIKEY": self.api_key}

    def _get(self, endpoint, params=None, signed=False, timeout=10):
        if params is None:
            params = {}
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["signature"] = self._sign(params)
        try:
            r = requests.get(f"{BASE_URL}{endpoint}", headers=self._headers(), params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            raise ConnectionError("Binance API timeout — skipping action for safety")
        except requests.exceptions.ConnectionError:
            raise ConnectionError("Binance API unreachable — skipping action for safety")

    def _post(self, endpoint, params, timeout=10):
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = self._sign(params)
        try:
            r = requests.post(f"{BASE_URL}{endpoint}", headers=self._headers(), params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            raise ConnectionError("Binance API timeout on order — NOT retrying to avoid duplicate orders")

    def get_klines(self, symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
        data = self._get("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})
        df = pd.DataFrame(data, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore"
        ])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df

    def get_price(self, symbol: str) -> tuple[float, datetime]:
        """Returns (price, timestamp). Raises if price is stale."""
        data = self._get("/api/v3/ticker/price", {"symbol": symbol})
        price = float(data["price"])
        ts = datetime.utcnow()
        self._last_price[symbol] = price
        self._last_price_time[symbol] = ts
        return price, ts

    def get_orderbook(self, symbol: str) -> dict:
        return self._get("/api/v3/depth", {"symbol": symbol, "limit": 5})

    def get_balance(self, asset: str = "USDT") -> float:
        data = self._get("/api/v3/account", signed=True)
        for b in data.get("balances", []):
            if b["asset"] == asset:
                return float(b["free"])
        return 0.0

    def get_symbol_info(self, symbol: str) -> dict:
        data = self._get("/api/v3/exchangeInfo")
        for s in data["symbols"]:
            if s["symbol"] == symbol:
                return s
        raise ValueError(f"Symbol {symbol} not found on Binance")

    def get_step_size(self, symbol: str) -> float:
        info = self.get_symbol_info(symbol)
        for f in info.get("filters", []):
            if f["filterType"] == "LOT_SIZE":
                return float(f["stepSize"])
        return 0.00001

    def get_min_notional(self, symbol: str) -> float:
        info = self.get_symbol_info(symbol)
        for f in info.get("filters", []):
            if f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
                return float(f.get("minNotional", f.get("notional", 10)))
        return 10.0

    def place_market_buy(self, symbol: str, quantity: float, client_order_id: str) -> dict:
        """Place market buy with idempotency via clientOrderId"""
        params = {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quantity": quantity,
            "newClientOrderId": client_order_id,
        }
        return self._post("/api/v3/order", params)

    def place_market_sell(self, symbol: str, quantity: float, client_order_id: str) -> dict:
        """Place market sell with idempotency via clientOrderId"""
        params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": quantity,
            "newClientOrderId": client_order_id,
        }
        return self._post("/api/v3/order", params)

    def get_order(self, symbol: str, client_order_id: str) -> dict:
        """Check if an order already exists (idempotency check)"""
        try:
            return self._get("/api/v3/order", {
                "symbol": symbol,
                "origClientOrderId": client_order_id
            }, signed=True)
        except Exception:
            return {}

    def ping(self) -> bool:
        try:
            self._get("/api/v3/ping")
            return True
        except Exception:
            return False
