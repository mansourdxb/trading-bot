import os
import requests
from datetime import datetime
from src.monitoring.logger import get_logger

logger = get_logger("Alerts")

def _send(message: str):
    token = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning("Telegram not configured — alert not sent")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        logger.error(f"Telegram alert failed: {e}")

def alert_startup(mode: str, pair: str, capital: float):
    _send(f"🤖 <b>Bot Started</b>\nMode: <b>{mode.upper()}</b>\nPair: {pair}\nCapital: ${capital}\nTime: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC")

def alert_buy(symbol, price, qty, usdt, sl, tp, confidence, mode):
    mode_tag = "📝 PAPER" if mode == "paper" else "🔴 LIVE"
    _send(f"🟢 <b>BUY | {mode_tag}</b>\n{symbol} @ ${price:,.2f}\nQty: {qty} | Used: ${usdt:.2f}\nSL: ${sl:,.2f} | TP: ${tp:,.2f}\nConfidence: {confidence:.0%}\n{datetime.utcnow().strftime('%H:%M UTC')}")

def alert_sell(symbol, price, buy_price, pnl, reason, mode):
    mode_tag = "📝 PAPER" if mode == "paper" else "🔴 LIVE"
    emoji = "💰" if pnl >= 0 else "🔴"
    _send(f"{emoji} <b>SELL | {mode_tag}</b>\n{symbol} @ ${price:,.2f}\nBuy: ${buy_price:,.2f} | PnL: ${pnl:+.2f}\nReason: {reason}\n{datetime.utcnow().strftime('%H:%M UTC')}")

def alert_risk_block(reason: str):
    _send(f"🚫 <b>Trade Blocked by Risk Manager</b>\n{reason}\n{datetime.utcnow().strftime('%H:%M UTC')}")

def alert_error(error: str):
    _send(f"⚠️ <b>Bot Error</b>\n{error}\n{datetime.utcnow().strftime('%H:%M UTC')}")

def alert_daily_summary(summary: dict):
    _send(
        f"📊 <b>Daily Summary</b>\n"
        f"Trades: {summary.get('total_trades', 0)}\n"
        f"Win Rate: {summary.get('win_rate_pct', 0)}%\n"
        f"Total PnL: ${summary.get('total_pnl', 0):+.2f}\n"
        f"Profit Factor: {summary.get('profit_factor', 0)}\n"
        f"{datetime.utcnow().strftime('%Y-%m-%d')}"
    )

def alert_kill_switch():
    _send("🔴 <b>KILL SWITCH ACTIVATED</b>\nAll trading halted immediately.")

def alert_drawdown_breach(drawdown_pct: float):
    _send(f"🚨 <b>MAX DRAWDOWN BREACHED</b>\nDrawdown: {drawdown_pct:.2f}%\nLive trading DISABLED.\nManual review required.")
