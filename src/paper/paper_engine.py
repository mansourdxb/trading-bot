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
from src.monitoring.alerts import alert_startup, alert_buy, alert_sell, alert_risk_block, alert_error, alert_daily_summary

logger = get_logger("PaperEngine")

class PaperEngine:
    def __init__(self):
        self.client = BinanceClient()
        self.market = MarketData(self.client)
        self.risk = RiskManager()
        self.executor = OrderExecutor(self.client)
        self.portfolio = PortfolioManager()
        self.last_daily_summary = datetime.utcnow().date()

    def run(self):
        logger.info("=" * 55)
        logger.info("PAPER TRADING ENGINE STARTED")
        logger.info(f"Pair: {TRADING['pair']} | Timeframe: {TRADING['timeframe']}")
        logger.info("No real money is used in this mode.")
        logger.info("=" * 55)

        alert_startup("paper", TRADING["pair"], TRADING["capital_limit_usdt"])

        while True:
            try:
                self._tick()
            except KeyboardInterrupt:
                logger.info("Paper engine stopped by user.")
                break
            except Exception as e:
                logger.error(f"Tick error: {e}")
                alert_error(str(e))

            today = datetime.utcnow().date()
            if today != self.last_daily_summary:
                alert_daily_summary(self.portfolio.get_summary())
                self.last_daily_summary = today

            interval = self._interval_seconds()
            logger.info(f"Sleeping {TRADING['timeframe']}...")
            time.sleep(interval)

    def _tick(self):
        ok, msg = self.risk.check_kill_switch()
        if not ok:
            logger.warning(msg)
            return

        df = self.market.get_candles(limit=100)
        price = self.market.get_price_with_freshness(max_age_seconds=RISK["stale_price_seconds"])
        result = analyze(df)

        # Always print heartbeat so you know bot is alive
        logger.info(f"---- Heartbeat | Price: ${price:,.2f} | Signal: {result.signal} | Confidence: {result.confidence:.2f} | RSI: {result.indicators.get('rsi', 'N/A')} | Next check in {TRADING['timeframe']} ----")

        if self.portfolio.has_position():
            pos = self.portfolio.get_position()
            sl = self.risk.get_stop_loss(pos["buy_price"])
            tp = self.risk.get_take_profit(pos["buy_price"])
            opened_at = datetime.fromisoformat(pos["opened_at"])
            held_hours = (datetime.utcnow() - opened_at).total_seconds() / 3600
            unrealized = (price - pos["buy_price"]) * pos["quantity"]
            logger.info(f"---- Position Open | Buy: ${pos['buy_price']:,.2f} | SL: ${sl:,.2f} | TP: ${tp:,.2f} | Unrealized PnL: ${unrealized:+.2f} ----")

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
                sell = self.executor.execute_sell(TRADING["pair"], pos["quantity"], pos["buy_price"], paper_mode=True)
                self.risk.on_trade_close(sell["pnl"])
                self.portfolio.close_position(price, sell["pnl"], exit_reason)
                alert_sell(TRADING["pair"], price, pos["buy_price"], sell["pnl"], exit_reason, "paper")

        else:
            if result.signal == "BUY":
                balance = TRADING["capital_limit_usdt"]
                usdt_to_use = self.risk.calculate_position_size(balance)
                allowed, reason = self.risk.can_open_position(usdt_to_use, result.confidence)

                if not allowed:
                    logger.warning(f"Trade blocked: {reason}")
                    alert_risk_block(reason)
                    return

                buy = self.executor.execute_buy(TRADING["pair"], usdt_to_use, paper_mode=True)
                self.risk.on_trade_open()
                self.portfolio.open_position(TRADING["pair"], buy["filled_price"], buy["quantity"], usdt_to_use)
                sl = self.risk.get_stop_loss(buy["filled_price"])
                tp = self.risk.get_take_profit(buy["filled_price"])
                alert_buy(TRADING["pair"], buy["filled_price"], buy["quantity"], usdt_to_use, sl, tp, result.confidence, "paper")

    def _interval_seconds(self):
        mapping = {
            "1m": 60, "3m": 180, "5m": 300, "10m": 600,
            "15m": 900, "30m": 1800, "1h": 3600,
            "4h": 14400, "1d": 86400
        }
        return mapping.get(TRADING["timeframe"], 900)
