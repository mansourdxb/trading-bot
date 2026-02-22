"""
Trading Bot Entry Point
=======================
WARNING: Trading involves risk. Profits are NOT guaranteed.
This bot can lose money. Never invest more than you can afford to lose entirely.

Usage:
  python run.py --backtest          # Test strategy on historical data first
  python run.py --paper             # Paper trade (default, no real money)
  python run.py --live --i-understand-risks   # Live trading (real money)
"""
import argparse
import sys
import os
from dotenv import load_dotenv

load_dotenv()

# Print Railway server IP on startup (for Binance IP whitelist)
try:
    import requests as _r
    _ip = _r.get("https://api.ipify.org", timeout=5).text
    print(f"SERVER_IP: {_ip}")
except Exception:
    print("SERVER_IP: Could not detect")

def main():
    parser = argparse.ArgumentParser(description="Binance Trading Bot - Safety First")
    parser.add_argument("--backtest", action="store_true", help="Run backtest on historical data")
    parser.add_argument("--paper", action="store_true", help="Run paper trading (default, no real money)")
    parser.add_argument("--live", action="store_true", help="Run live trading (real money)")
    parser.add_argument("--i-understand-risks", action="store_true", help="Required confirmation for live mode")
    args = parser.parse_args()

    if not args.backtest and not args.paper and not args.live:
        args.paper = True

    if args.backtest:
        print("\n" + "="*55)
        print("  BACKTEST MODE")
        print("="*55)
        from src.exchange.binance_client import BinanceClient
        from src.backtest.backtester import Backtester
        client = BinanceClient()
        bt = Backtester(client)
        results = bt.run()
        print("\nBacktest complete. Review results above before proceeding to paper or live trading.")

    elif args.paper:
        print("\n" + "="*55)
        print("  PAPER TRADING MODE - No real money used")
        print("="*55)
        from src.paper.paper_engine import PaperEngine
        engine = PaperEngine()
        engine.run()

    elif args.live:
        if not args.i_understand_risks:
            print("\nLIVE MODE BLOCKED")
            print("You must explicitly acknowledge risks to trade live.")
            print("Run with: python run.py --live --i-understand-risks")
            print("\nWARNING: Live trading uses REAL money.")
            print("WARNING: Profits are NOT guaranteed. You can lose your capital.")
            sys.exit(1)

        print("\n" + "="*55)
        print("  WARNING: LIVE TRADING MODE - REAL MONEY")
        print("  Profits are NOT guaranteed.")
        print("  Set KILL_SWITCH=true in .env to stop instantly.")
        print("="*55)

        confirm = input("\nType 'YES I ACCEPT THE RISK' to proceed: ")
        if confirm != "YES I ACCEPT THE RISK":
            print("Aborted.")
            sys.exit(0)

        from src.live.live_runner import LiveRunner
        runner = LiveRunner()
        runner.run()

if __name__ == "__main__":
    main()