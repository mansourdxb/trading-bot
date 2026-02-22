import os
import json
from datetime import datetime, date
from config import RISK, TRADING
from src.monitoring.logger import get_logger

logger = get_logger("RiskManager")

STATE_FILE = "risk_state.json"

class RiskManager:
    """
    Central safety enforcer. Every trade must be approved by this module.
    If in doubt, it BLOCKS the trade and logs why.
    """

    def __init__(self):
        self.state = self._load_state()
        self.equity = TRADING["capital_limit_usdt"]

    def _load_state(self) -> dict:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                return json.load(f)
        return {
            "daily_pnl": 0.0,
            "daily_date": str(date.today()),
            "max_drawdown_seen": 0.0,
            "peak_equity": TRADING["capital_limit_usdt"],
            "consecutive_losses": 0,
            "cooldown_until": None,
            "live_trading_disabled": False,
            "total_positions": 0
        }

    def _save_state(self):
        with open(STATE_FILE, "w") as f:
            json.dump(self.state, f, indent=2)

    def _reset_daily_if_new_day(self):
        today = str(date.today())
        if self.state["daily_date"] != today:
            self.state["daily_date"] = today
            self.state["daily_pnl"] = 0.0
            logger.info("New trading day — daily loss counter reset")
            self._save_state()

    def check_kill_switch(self) -> tuple[bool, str]:
        """Hard stop — overrides everything"""
        if os.getenv("KILL_SWITCH", "false").lower() == "true":
            return False, "KILL SWITCH is ON — all trading disabled"
        return True, ""

    def can_open_position(self, usdt_amount: float, signal_confidence: float) -> tuple[bool, str]:
        """
        Full pre-trade safety check. Returns (allowed, reason).
        All checks must pass or trade is blocked.
        """
        self._reset_daily_if_new_day()

        # 1. Kill switch
        ok, msg = self.check_kill_switch()
        if not ok:
            return False, msg

        # 2. Live trading disabled by drawdown
        if self.state["live_trading_disabled"]:
            return False, "Live trading disabled — max drawdown breached. Manual review required."

        # 3. Cooldown check
        if self.state["cooldown_until"]:
            cooldown_end = datetime.fromisoformat(self.state["cooldown_until"])
            if datetime.utcnow() < cooldown_end:
                remaining = (cooldown_end - datetime.utcnow()).seconds // 60
                return False, f"Cooldown active — {remaining} minutes remaining after {RISK['consecutive_loss_limit']} consecutive losses"

        # 4. Daily loss cap
        daily_loss_pct = abs(self.state["daily_pnl"]) / self.equity * 100 if self.state["daily_pnl"] < 0 else 0
        if daily_loss_pct >= RISK["daily_loss_cap_pct"]:
            return False, f"Daily loss cap hit ({daily_loss_pct:.2f}% >= {RISK['daily_loss_cap_pct']}%) — no more trades today"

        # 5. Concurrent positions
        if self.state["total_positions"] >= RISK["max_concurrent_positions"]:
            return False, f"Max concurrent positions reached ({RISK['max_concurrent_positions']})"

        # 6. Max exposure
        exposure_pct = (usdt_amount / self.equity) * 100
        if exposure_pct > RISK["max_total_exposure_pct"]:
            return False, f"Position too large ({exposure_pct:.1f}% > {RISK['max_total_exposure_pct']}% max exposure)"

        # 7. Signal confidence
        if signal_confidence < RISK["min_signal_confidence"]:
            return False, f"Signal confidence too low ({signal_confidence:.2f} < {RISK['min_signal_confidence']})"

        # 8. Minimum order size
        if usdt_amount < TRADING["min_order_usdt"]:
            return False, f"Order too small (${usdt_amount:.2f} < ${TRADING['min_order_usdt']} minimum)"

        logger.info(f"Pre-trade checks PASSED | Amount: ${usdt_amount:.2f} | Confidence: {signal_confidence:.2f}")
        return True, "All checks passed"

    def calculate_position_size(self, equity: float) -> float:
        """Risk-based position sizing: never risk more than risk_per_trade_pct"""
        self.equity = min(equity, TRADING["capital_limit_usdt"])
        risk_amount = self.equity * (RISK["risk_per_trade_pct"] / 100)
        return round(risk_amount, 2)

    def on_trade_open(self):
        self.state["total_positions"] += 1
        self._save_state()

    def on_trade_close(self, pnl_usdt: float):
        """Update risk state after a trade closes"""
        self.state["total_positions"] = max(0, self.state["total_positions"] - 1)
        self.state["daily_pnl"] = round(self.state["daily_pnl"] + pnl_usdt, 4)

        # Update drawdown
        self.equity += pnl_usdt
        if self.equity > self.state["peak_equity"]:
            self.state["peak_equity"] = self.equity

        drawdown_pct = ((self.state["peak_equity"] - self.equity) / self.state["peak_equity"]) * 100
        self.state["max_drawdown_seen"] = max(self.state["max_drawdown_seen"], drawdown_pct)

        # Consecutive losses
        if pnl_usdt < 0:
            self.state["consecutive_losses"] += 1
            logger.warning(f"Consecutive losses: {self.state['consecutive_losses']}")

            if self.state["consecutive_losses"] >= RISK["consecutive_loss_limit"]:
                from datetime import timedelta
                cooldown_end = datetime.utcnow() + timedelta(minutes=RISK["cooldown_minutes"])
                self.state["cooldown_until"] = cooldown_end.isoformat()
                logger.warning(f"Consecutive loss limit hit — cooldown until {cooldown_end}")
        else:
            self.state["consecutive_losses"] = 0
            self.state["cooldown_until"] = None

        # Max drawdown breach — disable live trading
        if drawdown_pct >= RISK["max_drawdown_cap_pct"]:
            self.state["live_trading_disabled"] = True
            logger.critical(f"MAX DRAWDOWN BREACHED ({drawdown_pct:.2f}%) — live trading DISABLED. Manual review required.")

        self._save_state()
        logger.info(f"Trade closed | PnL: ${pnl_usdt:+.2f} | Daily PnL: ${self.state['daily_pnl']:+.2f} | Drawdown: {drawdown_pct:.2f}%")

    def get_stop_loss(self, buy_price: float) -> float:
        return round(buy_price * (1 - RISK["stop_loss_pct"] / 100), 2)

    def get_take_profit(self, buy_price: float) -> float:
        return round(buy_price * (1 + RISK["take_profit_pct"] / 100), 2)

    def get_status(self) -> dict:
        self._reset_daily_if_new_day()
        return {
            "daily_pnl": self.state["daily_pnl"],
            "max_drawdown_seen": self.state["max_drawdown_seen"],
            "consecutive_losses": self.state["consecutive_losses"],
            "live_trading_disabled": self.state["live_trading_disabled"],
            "kill_switch": os.getenv("KILL_SWITCH", "false").lower() == "true"
        }
