"""
LIVE TRADING ENGINE
⚠️ WARNING: This uses real money. Losses are possible and not guaranteed to be recovered.
This mode requires:
  - Explicit --live --i-understand-risks flags
  - Valid API keys in .env
  - Risk config present
  - Kill switch configured
  - Passing backtest results
"""
import os
import time
from datetime import datetime
from config import RISK, TRADING
from src.exchange.binance_client import BinanceClient
from src.data.market_data import MarketData
from src.strategy.ema_rsi_macd import analyze
from src.risk.risk_manager import RiskManager
from src.execution.order_executor import OrderExecutor
from src.portfolio.portfolio_manager import PortfolioManager
from src.monitoring.logger import get_logger
from src.monitoring.alerts import alert_startup, alert_buy, alert_sell, alert_risk_block, alert_error, alert_daily_summary, alert_kill_switch

logger = get_logger("LiveRunner")

class LiveRunner:
    def __init__(self):
        self._pre_flight_checks()
        self.client = BinanceClient()
        self.market = MarketData(self.client)
        self.risk = RiskManager()
        self.executor = OrderExecutor(self.client)
        self.portfolio = PortfolioManager()
        self.last_daily_summary = datetime.utcnow().date()

    def _pre_flight_checks(self):
        """All checks must pass or live mode will NOT start"""
        errors = []

        # API keys
        if not os.getenv("BINANCE_API_KEY") or not os.getenv("BINANCE_SECRET_KEY"):
            errors.append("Missing BINANCE_API_KEY or BINANCE_SECRET_KEY in .env")

        # Telegram
        if not os.getenv("TELEGRAM_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
            errors.append("Missing TELEGRAM_TOKEN or TELEGRAM_CHAT_ID — alerts required for live mode")

        # Kill switch must be present (even if false)
        if os.getenv("KILL_SWITCH") is None:
            errors.append("KILL_SWITCH not set in .env — required for live mode")

        # Must not be already disabled by drawdown
        risk = RiskManager()
        status = risk.get_status()
        if status["live_trading_disabled"]:
            errors.append("Live trading is DISABLED due to max drawdown breach — manual reset required")

        if errors:
            for e in errors:
                logger.critical(f"PRE-FLIGHT FAILED: {e}")
            raise SystemExit("Live mode blocked — fix errors above before proceeding.")

        logger.info("✅ All pre-flight checks passed")

    def run(self):
        logger.info("=" * 55)
        logger.warning("⚠️  LIVE TRADING MODE — REAL MONEY AT RISK")
        logger.warning("⚠️  Profits are NOT guaranteed.")
        logger.warning("⚠️  Set KILL_SWITCH=true in .env to stop immediately.")
        logger.info(f"Pair: {TRADING['pair']} | Timeframe: {TRADING['timeframe']}")
        logger.info(f"Capital limit: ${TRADING['capital_limit_usdt']} USDT")
        logger.info("=" * 55)

        alert_startup("LIVE", TRADING["pair"], TRADING["capital_limit_usdt"])

        while True:
            try:
                # Kill switch on every cycle
                ok, msg = self.risk.check_kill_switch()
                if not ok:
                    logger.critical(msg)
                    alert_kill_switch()
                    break

                self._tick()

            except KeyboardInterrupt:
                logger.info("Live engine stopped by user.")
                break
            except Exception as e:
                logger.error(f"Tick error: {e}")
                alert_error(str(e))

            today = datetime.utcnow().date()
            if today != self.last_daily_summary:
                alert_daily_summary(self.portfolio.get_summary())
                self.last_daily_summary = today

            time.sleep(self._interval_seconds())

    def _tick(self):
        df = self.market.get_candles(limit=100)
        price = self.market.get_price_with_freshness(max_age_seconds=RISK["stale_price_seconds"])
        result = analyze(df)

        logger.info(f"[LIVE] Price: ${price:,.2f} | Signal: {result.signal} | Confidence: {result.confidence:.2f}")

        if self.portfolio.has_position():
            pos = self.portfolio.get_position()
            sl = self.risk.get_stop_loss(pos["buy_price"])
            tp = self.risk.get_take_profit(pos["buy_price"])
            opened_at = datetime.fromisoformat(pos["opened_at"])
            held_hours = (datetime.utcnow() - opened_at).total_seconds() / 3600

            exit_reason = None
            if price <= sl:
                exit_reason = "Stop Loss"
            elif price >= tp:
                exit_reason = "Take Profit"
            elif result.signal == "SELL":
                exit_reason = "Strategy Signal"
            elif held_hours >= RISK["max_holding_hours"]:
                exit_reason = "Max Hold Time"

            if exit_reason:
                sell = self.executor.execute_sell(TRADING["pair"], pos["quantity"], pos["buy_price"], paper_mode=False)
                self.risk.on_trade_close(sell["pnl"])
                self.portfolio.close_position(price, sell["pnl"], exit_reason)
                alert_sell(TRADING["pair"], price, pos["buy_price"], sell["pnl"], exit_reason, "live")
        else:
            if result.signal == "BUY":
                balance = self.client.get_balance("USDT")
                usdt_to_use = self.risk.calculate_position_size(balance)
                allowed, reason = self.risk.can_open_position(usdt_to_use, result.confidence)

                if not allowed:
                    logger.warning(f"Trade blocked: {reason}")
                    alert_risk_block(reason)
                    return

                buy = self.executor.execute_buy(TRADING["pair"], usdt_to_use, paper_mode=False)
                self.risk.on_trade_open()
                self.portfolio.open_position(TRADING["pair"], buy["filled_price"], buy["quantity"], usdt_to_use)
                sl = self.risk.get_stop_loss(buy["filled_price"])
                tp = self.risk.get_take_profit(buy["filled_price"])
                alert_buy(TRADING["pair"], buy["filled_price"], buy["quantity"], usdt_to_use, sl, tp, result.confidence, "live")

    def _interval_seconds(self):
        mapping = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
        return mapping.get(TRADING["timeframe"], 3600)
