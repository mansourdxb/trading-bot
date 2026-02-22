import math
import uuid
from datetime import datetime
from config import RISK, TRADING
from src.exchange.binance_client import BinanceClient
from src.monitoring.logger import get_logger

logger = get_logger("OrderExecutor")

class OrderExecutor:
    def __init__(self, client: BinanceClient):
        self.client = client

    def _round_qty(self, qty: float, step_size: float) -> float:
        precision = int(round(-math.log(step_size, 10), 0))
        return round(qty, precision)

    def _estimate_cost(self, usdt_amount: float) -> float:
        """Estimate total cost including fees and slippage"""
        fee = usdt_amount * (RISK["fee_pct"] / 100) * 2  # both sides
        slippage = usdt_amount * (RISK["slippage_estimate_pct"] / 100)
        return round(fee + slippage, 4)

    def check_spread(self, symbol: str) -> tuple[bool, float]:
        """Returns (ok, spread_pct)"""
        try:
            book = self.client.get_orderbook(symbol)
            bid = float(book["bids"][0][0])
            ask = float(book["asks"][0][0])
            spread_pct = ((ask - bid) / ((ask + bid) / 2)) * 100
            ok = spread_pct <= RISK["max_spread_pct"]
            return ok, round(spread_pct, 4)
        except Exception as e:
            logger.error(f"Spread check failed: {e}")
            return False, 999.0

    def execute_buy(self, symbol: str, usdt_amount: float, paper_mode: bool = True) -> dict:
        """
        Execute a buy order safely.
        Returns order result dict with filled_price, quantity, fees_estimated.
        """
        # Safety: cap at hard limit
        usdt_amount = min(usdt_amount, TRADING["capital_limit_usdt"])

        # Check spread
        spread_ok, spread_pct = self.check_spread(symbol)
        if not spread_ok:
            raise ValueError(f"Spread too wide ({spread_pct}%) — order blocked")

        # Get price
        price, ts = self.client.get_price(symbol)
        age = (datetime.utcnow() - ts).total_seconds()
        if age > RISK["stale_price_seconds"]:
            raise ValueError(f"Stale price ({age:.1f}s) — order blocked")

        step_size = self.client.get_step_size(symbol)
        raw_qty = usdt_amount / price
        quantity = self._round_qty(raw_qty, step_size)
        fees_estimated = self._estimate_cost(usdt_amount)

        if paper_mode:
            logger.info(f"[PAPER] BUY {quantity} {symbol} @ ${price:,.2f} | USDT: ${usdt_amount:.2f} | Est. fees: ${fees_estimated:.3f}")
            return {
                "mode": "paper",
                "symbol": symbol,
                "side": "BUY",
                "quantity": quantity,
                "filled_price": price,
                "usdt_used": usdt_amount,
                "fees_estimated": fees_estimated,
                "timestamp": datetime.utcnow().isoformat()
            }

        # LIVE: use client order ID for idempotency (no duplicate orders)
        client_order_id = f"bot_buy_{uuid.uuid4().hex[:16]}"

        # Check if order already exists before placing
        existing = self.client.get_order(symbol, client_order_id)
        if existing:
            logger.warning(f"Order {client_order_id} already exists — skipping to avoid duplicate")
            return existing

        order = self.client.place_market_buy(symbol, quantity, client_order_id)
        filled_price = float(order.get("fills", [{}])[0].get("price", price))
        filled_qty = float(order.get("executedQty", quantity))

        logger.info(f"[LIVE] BUY executed | {filled_qty} {symbol} @ ${filled_price:,.2f} | OrderID: {order.get('orderId')}")
        return {
            "mode": "live",
            "symbol": symbol,
            "side": "BUY",
            "quantity": filled_qty,
            "filled_price": filled_price,
            "usdt_used": usdt_amount,
            "fees_estimated": fees_estimated,
            "order_id": order.get("orderId"),
            "client_order_id": client_order_id,
            "timestamp": datetime.utcnow().isoformat()
        }

    def execute_sell(self, symbol: str, quantity: float, buy_price: float, paper_mode: bool = True) -> dict:
        """Execute a sell order safely"""
        price, ts = self.client.get_price(symbol)
        age = (datetime.utcnow() - ts).total_seconds()
        if age > RISK["stale_price_seconds"]:
            raise ValueError(f"Stale price ({age:.1f}s) — sell blocked")

        step_size = self.client.get_step_size(symbol)
        quantity = self._round_qty(quantity, step_size)
        usdt_received = quantity * price
        fees_estimated = self._estimate_cost(usdt_received)
        pnl = (price - buy_price) * quantity - fees_estimated

        if paper_mode:
            logger.info(f"[PAPER] SELL {quantity} {symbol} @ ${price:,.2f} | PnL: ${pnl:+.2f}")
            return {
                "mode": "paper",
                "symbol": symbol,
                "side": "SELL",
                "quantity": quantity,
                "filled_price": price,
                "usdt_received": usdt_received,
                "fees_estimated": fees_estimated,
                "pnl": round(pnl, 4),
                "timestamp": datetime.utcnow().isoformat()
            }

        client_order_id = f"bot_sell_{uuid.uuid4().hex[:16]}"
        existing = self.client.get_order(symbol, client_order_id)
        if existing:
            logger.warning(f"Sell order {client_order_id} already exists — skipping duplicate")
            return existing

        order = self.client.place_market_sell(symbol, quantity, client_order_id)
        filled_price = float(order.get("fills", [{}])[0].get("price", price))
        filled_qty = float(order.get("executedQty", quantity))
        pnl = (filled_price - buy_price) * filled_qty - fees_estimated

        logger.info(f"[LIVE] SELL executed | {filled_qty} {symbol} @ ${filled_price:,.2f} | PnL: ${pnl:+.2f}")
        return {
            "mode": "live",
            "symbol": symbol,
            "side": "SELL",
            "quantity": filled_qty,
            "filled_price": filled_price,
            "usdt_received": filled_qty * filled_price,
            "fees_estimated": fees_estimated,
            "pnl": round(pnl, 4),
            "order_id": order.get("orderId"),
            "timestamp": datetime.utcnow().isoformat()
        }
