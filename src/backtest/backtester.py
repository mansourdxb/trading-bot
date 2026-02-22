from config import RISK, TRADING, BACKTEST, STRATEGY
from src.exchange.binance_client import BinanceClient
from src.strategy.ema_rsi_macd import analyze
from src.monitoring.logger import get_logger

logger = get_logger("Backtester")

class Backtester:
    def __init__(self, client: BinanceClient):
        self.client = client

    def run(self, symbol=None, interval=None, limit=None) -> dict:
        symbol = symbol or TRADING["pair"]
        interval = interval or TRADING["timeframe"]
        limit = limit or BACKTEST["candle_limit"]

        logger.info(f"Starting backtest: {symbol} {interval} | {limit} candles")
        df = self.client.get_klines(symbol, interval, limit)

        split = int(len(df) * 0.7)
        in_sample  = df.iloc[:split].reset_index(drop=True)
        out_sample = df.iloc[split:].reset_index(drop=True)

        logger.info(f"In-sample: {len(in_sample)} candles | Out-of-sample: {len(out_sample)} candles")

        in_results  = self._run_segment(in_sample,  "IN-SAMPLE")
        out_results = self._run_segment(out_sample, "OUT-OF-SAMPLE")

        logger.info("Walk-forward validation complete")
        if out_results.get("win_rate", 0) < 40 or out_results.get("profit_factor", 0) < 1.0:
            logger.warning("Strategy underperforms on out-of-sample data - review before going live")
        else:
            logger.info("Strategy PASSED out-of-sample validation")

        return {"in_sample": in_results, "out_sample": out_results}

    def _run_segment(self, df, label: str) -> dict:
        capital   = TRADING["capital_limit_usdt"]
        initial   = capital
        position  = None
        trades    = []
        fee_pct   = RISK["fee_pct"] / 100 if BACKTEST["include_fees"] else 0
        slip_pct  = RISK["slippage_estimate_pct"] / 100 if BACKTEST["include_slippage"] else 0
        total_fees = 0
        min_candles = STRATEGY["min_candles_required"]

        for i in range(min_candles, len(df) - 1):
            window = df.iloc[:i+1]
            result = analyze(window)
            price  = float(df.iloc[i]["close"])

            if position is None and result.signal == "BUY":
                usdt = capital * (RISK["risk_per_trade_pct"] / 100)
                if usdt < TRADING["min_order_usdt"]:
                    continue
                buy_price = price * (1 + slip_pct)
                fee = usdt * fee_pct
                total_fees += fee
                qty = (usdt - fee) / buy_price
                sl  = buy_price * (1 - RISK["stop_loss_pct"] / 100)
                tp  = buy_price * (1 + RISK["take_profit_pct"] / 100)
                position = {"buy_price": buy_price, "qty": qty, "usdt": usdt,
                            "sl": sl, "tp": tp, "entry_i": i}

            elif position is not None:
                exit_price = None
                reason = ""
                lo = float(df.iloc[i]["low"])
                hi = float(df.iloc[i]["high"])

                if lo <= position["sl"]:
                    exit_price = position["sl"] * (1 - slip_pct)
                    reason = "Stop Loss"
                elif hi >= position["tp"]:
                    exit_price = position["tp"] * (1 - slip_pct)
                    reason = "Take Profit"
                elif result.signal == "SELL":
                    exit_price = price * (1 - slip_pct)
                    reason = "Signal Exit"

                holding = i - position["entry_i"]
                if holding >= RISK["max_holding_hours"] and exit_price is None:
                    exit_price = price * (1 - slip_pct)
                    reason = "Max Hold Time"

                if exit_price:
                    fee = exit_price * position["qty"] * fee_pct
                    total_fees += fee
                    pnl = (exit_price - position["buy_price"]) * position["qty"] - fee
                    capital += pnl
                    trades.append({
                        "pnl": round(pnl, 4),
                        "pnl_pct": round(((exit_price - position["buy_price"]) / position["buy_price"]) * 100, 2),
                        "reason": reason,
                        "holding_candles": holding
                    })
                    position = None

        total_trades = len(trades)
        if total_trades == 0:
            logger.warning(f"[{label}] No trades generated")
            return {"total_trades": 0, "label": label, "win_rate": 0, "profit_factor": 0}

        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        win_rate = len(wins) / total_trades * 100
        gross_profit = sum(t["pnl"] for t in wins) if wins else 0
        gross_loss = abs(sum(t["pnl"] for t in losses)) if losses else 1e-10
        profit_factor = gross_profit / gross_loss
        total_return = ((capital - initial) / initial) * 100
        expectancy = (capital - initial) / total_trades
        avg_hold = sum(t["holding_candles"] for t in trades) / total_trades

        equity_curve = [initial]
        running = initial
        for t in trades:
            running += t["pnl"]
            equity_curve.append(running)
        peak = equity_curve[0]
        max_dd = 0
        for e in equity_curve:
            if e > peak:
                peak = e
            dd = (peak - e) / peak * 100
            max_dd = max(max_dd, dd)

        results = {
            "label": label,
            "total_trades": total_trades,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "total_return_pct": round(total_return, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "expectancy_usdt": round(expectancy, 2),
            "total_fees_usdt": round(total_fees, 2),
            "avg_holding_candles": round(avg_hold, 1),
            "initial_capital": initial,
            "final_capital": round(capital, 2)
        }
        self._print_results(results)
        return results

    def _print_results(self, r: dict):
        passed = (r["win_rate"] >= 45 and r["profit_factor"] >= 1.2 and r["max_drawdown_pct"] <= 15)
        status = "PASS" if passed else "FAIL - Do NOT trade live yet"
        logger.info(f"\n{'='*55}")
        logger.info(f"  {r['label']} | {'PASS' if passed else 'FAIL'}: {status}")
        logger.info(f"{'='*55}")
        logger.info(f"  Total Trades:       {r['total_trades']}")
        logger.info(f"  Wins / Losses:      {r['wins']} / {r['losses']}")
        logger.info(f"  Win Rate:           {r['win_rate']}%")
        logger.info(f"  Profit Factor:      {r['profit_factor']}")
        logger.info(f"  Total Return:       {r['total_return_pct']:+.2f}%")
        logger.info(f"  Max Drawdown:       {r['max_drawdown_pct']:.2f}%")
        logger.info(f"  Expectancy/trade:   ${r['expectancy_usdt']:+.2f}")
        logger.info(f"  Fees Paid:          ${r['total_fees_usdt']:.2f}")
        logger.info(f"  Avg Hold (candles): {r['avg_holding_candles']}")
        logger.info(f"  Final Capital:      ${r['final_capital']:.2f}")
        logger.info(f"{'='*55}")
        if not passed:
            logger.warning("Strategy did not meet minimum criteria. Review before proceeding.")
