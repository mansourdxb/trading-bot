import time
from datetime import datetime, timezone
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

# Canonical Binance-supported intervals only
_TIMEFRAME_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
    "8h": 28800, "12h": 43200, "1d": 86400, "3d": 259200,
    "1w": 604800,
}
_CLOSE_BUFFER_SECONDS = 2


def _timeframe_to_seconds(tf: str) -> int:
    if tf not in _TIMEFRAME_SECONDS:
        supported = ", ".join(_TIMEFRAME_SECONDS.keys())
        raise ValueError(f"Unsupported timeframe '{tf}'. Supported: {supported}")
    return _TIMEFRAME_SECONDS[tf]


def _next_candle_close_utc(tf_seconds: int) -> datetime:
    now_ts = datetime.now(timezone.utc).timestamp()
    next_boundary = (int(now_ts / tf_seconds) + 1) * tf_seconds
    return datetime.fromtimestamp(next_boundary + _CLOSE_BUFFER_SECONDS, tz=timezone.utc)


class PaperEngine:
    def __init__(self):
        self.client = BinanceClient()
        self.market = MarketData(self.client)
        self.risk = RiskManager()
        self.executor = OrderExecutor(self.client)
        self.portfolio = PortfolioManager()
        self.last_daily_summary = datetime.now(timezone.utc).date()
        self._last_processed_candle_close = None
        self._tf_seconds = _timeframe_to_seconds(TRADING["timeframe"])

    def run(self):
        logger.info("=" * 55)
        logger.info("PAPER TRADING ENGINE STARTED")
        logger.info(f"Pair: {TRADING['pair']} | Timeframe: {TRADING['timeframe']}")
        logger.info("No real money is used in this mode.")
        logger.info("=" * 55)

        alert_startup("paper", TRADING["pair"], TRADING["capital_limit_usdt"])

        while True:
            try:
                wake_at = _next_candle_close_utc(self._tf_seconds)
                now = datetime.now(timezone.utc)
                sleep_secs = max(0, (wake_at - now).total_seconds())
                logger.info(
                    f"Sleeping until next {TRADING['timeframe']} candle close at "
                    f"{wake_at.strftime('%Y-%m-%d %H:%M:%S')} UTC "
                    f"(in {int(sleep_secs)}s)"
                )
                time.sleep(sleep_secs)
                self._tick()

            except ValueError as e:
                logger.error(f"Fatal config error: {e}")
                alert_error(str(e))
                break
            except KeyboardInterrupt:
                logger.info("Paper engine stopped by user.")
                break
            except Exception as e:
                logger.error(f"Tick error: {e}")
                alert_error(str(e))
                time.sleep(10)

            today = datetime.now(timezone.utc).date()
            if today != self.last_daily_summary:
                alert_daily_summary(self.portfolio.get_summary())
                self.last_daily_summary = today

    def _tick(self):
        ok, msg = self.risk.check_kill_switch()
        if not ok:
            logger.warning(msg)
            return

        df = self.market.get_candles(limit=101)
        now_ms = datetime.now(timezone.utc).timestamp() * 1000

        # Exclude currently-open candle
        df = df[df["close_time"] < now_ms].copy()

        if df.empty:
            logger.warning("No closed candles available — skipping tick")
            return

        last_closed_ts_ms = int(df.iloc[-1]["close_time"])
        last_closed_dt = datetime.fromtimestamp(last_closed_ts_ms / 1000, tz=timezone.utc)

        # Dedup: skip if already processed this candle
        if self._last_processed_candle_close == last_closed_ts_ms:
            logger.info(f"Already processed candle close {last_closed_dt.strftime('%H:%M:%S')} UTC — skipping")
            return

        result = analyze(df)
        price = self.market.get_price_with_freshness(max_age_seconds=RISK["stale_price_seconds"])

        pos_status = "OPEN" if self.portfolio.has_position() else "NONE"
        equity_str = ""
        if self.portfolio.has_position():
            pos = self.portfolio.get_position()
            unrealized = (price - pos["buy_price"]) * pos["quantity"]
            equity_str = f" | unrealized: ${unrealized:+.2f}"

        logger.info(
            f"---- Heartbeat | Price: ${price:,.2f} | Signal: {result.signal} | "
            f"Confidence: {result.confidence:.2f} | RSI: {result.indicators.get('rsi', 'N/A')} | "
            f"last_closed_candle: {last_closed_dt.strftime('%H:%M:%S')} UTC | "
            f"analyzed_candle: {last_closed_dt.strftime('%H:%M:%S')} UTC | "
            f"position: {pos_status}{equity_str} ----"
        )

        self._last_processed_candle_close = last_closed_ts_ms

        if self.portfolio.has_position():
            pos = self.portfolio.get_position()
            sl = self.risk.get_stop_loss(pos["buy_price"])
            tp = self.risk.get_take_profit(pos["buy_price"])
            opened_at = datetime.fromisoformat(pos["opened_at"])
            held_hours = (datetime.utcnow() - opened_at).total_seconds() / 3600

            exit_reason = None
            lo = float(df.iloc[-1]["low"])
            hi = float(df.iloc[-1]["high"])

            if lo <= sl:
                exit_reason = "Stop Loss"
            elif hi >= tp:
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
