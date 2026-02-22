RISK = {
    "risk_per_trade_pct": 15.0,
    "max_total_exposure_pct": 50.0,
    "max_concurrent_positions": 1,
    "daily_loss_cap_pct": 2.0,
    "max_drawdown_cap_pct": 8.0,
    "consecutive_loss_limit": 3,
    "cooldown_minutes": 120,
    "min_signal_confidence": 0.55,
    "max_spread_pct": 0.5,
    "stale_price_seconds": 30,
    "stop_loss_pct": 1.5,
    "take_profit_pct": 3.0,
    "max_holding_hours": 48,
    "fee_pct": 0.1,
    "slippage_estimate_pct": 0.05,
}

TRADING = {
    "pair": "ETHUSDT",
    "timeframe": "15m",
    "capital_limit_usdt": 100,
    "min_order_usdt": 5,
}

STRATEGY = {
    "ema_fast": 9,
    "ema_slow": 21,
    "rsi_period": 14,
    "rsi_overbought": 70,
    "rsi_oversold": 30,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "min_candles_required": 50,
}

BACKTEST = {
    "candle_limit": 2000,
    "include_fees": True,
    "include_slippage": True,
}
