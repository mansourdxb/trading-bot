import pandas as pd
from dataclasses import dataclass
from config import STRATEGY

@dataclass
class SignalResult:
    signal: str
    confidence: float
    reason: str
    indicators: dict

def _ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def _rsi(series, period):
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period - 1, min_periods=period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))

def _macd(series):
    ema_f = _ema(series, STRATEGY["macd_fast"])
    ema_s = _ema(series, STRATEGY["macd_slow"])
    line = ema_f - ema_s
    signal = _ema(line, STRATEGY["macd_signal"])
    return line, signal, line - signal

def _atr(df, period=14):
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def analyze(df):
    if len(df) < STRATEGY["min_candles_required"]:
        return SignalResult("HOLD", 0.0, "Not enough candles", {})

    close = df["close"]
    ef = _ema(close, STRATEGY["ema_fast"])
    es = _ema(close, STRATEGY["ema_slow"])
    rsi = _rsi(close, STRATEGY["rsi_period"])
    macd_line, macd_sig, _ = _macd(close)
    atr = _atr(df)

    ef1, es1 = ef.iloc[-1], es.iloc[-1]
    ef2, es2 = ef.iloc[-2], es.iloc[-2]
    r = rsi.iloc[-1]
    ml1, ms1 = macd_line.iloc[-1], macd_sig.iloc[-1]
    ml2, ms2 = macd_line.iloc[-2], macd_sig.iloc[-2]
    atr_pct = (atr.iloc[-1] / close.iloc[-1]) * 100
    price = close.iloc[-1]

    indicators = {
        "ema_fast": round(ef1, 2),
        "ema_slow": round(es1, 2),
        "rsi": round(r, 2),
        "macd": round(ml1, 4),
        "macd_signal": round(ms1, 4),
        "atr_pct": round(atr_pct, 4),
        "price": round(price, 2)
    }

    if atr_pct < 0.02:  # lowered from 0.03
        return SignalResult("HOLD", 0.0, "Volatility too low", indicators)

    uptrend        = ef1 > es1
    downtrend      = ef1 < es1
    ema_cross_up   = ef2 <= es2 and ef1 > es1
    ema_cross_down = ef2 >= es2 and ef1 < es1
    macd_bullish   = ml1 > ms1
    macd_bearish   = ml1 < ms1
    macd_cross_up  = ml2 <= ms2 and ml1 > ms1
    macd_cross_down= ml2 >= ms2 and ml1 < ms1
    rsi_ok_buy     = r < STRATEGY["rsi_overbought"]   # < 70
    rsi_ok_sell    = r > STRATEGY["rsi_oversold"]      # > 30

    # --- STRONG BUY signals (0.80) ---
    if ema_cross_up and rsi_ok_buy and macd_bullish:
        return SignalResult("BUY", 0.80, "EMA cross up + MACD bullish", indicators)

    # --- MEDIUM BUY signals (0.65) ---
    if uptrend and macd_cross_up and r < 60:
        return SignalResult("BUY", 0.65, "Uptrend + MACD cross up", indicators)
    if macd_cross_up and r < 45:                      # NEW: oversold bounce
        return SignalResult("BUY", 0.65, "Oversold MACD cross up", indicators)

    # --- RELAXED BUY signals (0.60) ---
    if uptrend and macd_bullish and r < 55:           # was r < 50
        return SignalResult("BUY", 0.60, "Uptrend + MACD bullish", indicators)
    if macd_bullish and r < 35 and not downtrend:     # NEW: deep oversold
        return SignalResult("BUY", 0.60, "Deep oversold + MACD bullish", indicators)

    # --- STRONG SELL signals (0.80) ---
    if ema_cross_down and rsi_ok_sell and macd_bearish:
        return SignalResult("SELL", 0.80, "EMA cross down + MACD bearish", indicators)

    # --- MEDIUM SELL signals (0.65) ---
    if downtrend and macd_cross_down and r > 40:
        return SignalResult("SELL", 0.65, "Downtrend + MACD cross down", indicators)
    if macd_cross_down and r > 55:                    # NEW: overbought drop
        return SignalResult("SELL", 0.65, "Overbought MACD cross down", indicators)

    # --- RELAXED SELL signals (0.60) ---
    if downtrend and macd_bearish and r > 45:         # was r > 50
        return SignalResult("SELL", 0.60, "Downtrend + MACD bearish", indicators)
    if macd_bearish and r > 65 and not uptrend:       # NEW: overbought exit
        return SignalResult("SELL", 0.60, "Overbought + MACD bearish", indicators)

    return SignalResult("HOLD", 0.0, "No confirmed signal", indicators)
