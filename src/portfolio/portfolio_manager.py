import json
import os
from datetime import datetime
from src.monitoring.logger import get_logger

logger = get_logger("PortfolioManager")
PORTFOLIO_FILE = "portfolio_state.json"

class PortfolioManager:
    def __init__(self):
        self.state = self._load()

    def _load(self) -> dict:
        if os.path.exists(PORTFOLIO_FILE):
            with open(PORTFOLIO_FILE) as f:
                return json.load(f)
        return {"position": None, "trade_history": []}

    def _save(self):
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(self.state, f, indent=2, default=str)

    def has_position(self) -> bool:
        return self.state["position"] is not None

    def open_position(self, symbol: str, buy_price: float, quantity: float, usdt_used: float):
        self.state["position"] = {
            "symbol": symbol,
            "buy_price": buy_price,
            "quantity": quantity,
            "usdt_used": usdt_used,
            "opened_at": datetime.utcnow().isoformat()
        }
        self._save()
        logger.info(f"Position opened: {quantity} {symbol} @ ${buy_price:,.2f}")

    def close_position(self, sell_price: float, pnl: float, reason: str):
        if not self.state["position"]:
            return
        pos = self.state["position"]
        trade = {**pos, "sell_price": sell_price, "pnl": pnl,
                 "reason": reason, "closed_at": datetime.utcnow().isoformat()}
        self.state["trade_history"].append(trade)
        self.state["position"] = None
        self._save()
        logger.info(f"Position closed | PnL: ${pnl:+.2f} | Reason: {reason}")

    def get_position(self) -> dict:
        return self.state["position"]

    def get_summary(self) -> dict:
        history = self.state["trade_history"]
        if not history:
            return {"total_trades": 0}
        wins = [t for t in history if t["pnl"] > 0]
        losses = [t for t in history if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in history)
        gross_profit = sum(t["pnl"] for t in wins) if wins else 0
        gross_loss = abs(sum(t["pnl"] for t in losses)) if losses else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        win_rate = len(wins) / len(history) * 100 if history else 0
        return {
            "total_trades": len(history),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(win_rate, 1),
            "total_pnl": round(total_pnl, 2),
            "profit_factor": round(profit_factor, 2),
            "avg_pnl_per_trade": round(total_pnl / len(history), 2)
        }
