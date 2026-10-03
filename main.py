import os
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, time, timezone
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import asyncio

# ====================== CONFIG ======================
BOT_TOKEN = os.getenv("BOT_TOKEN")          # We will set this on Railway
CHAT_ID = None

FOREX = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X", "AUDUSD=X", "USDCAD=X", "NZDUSD=X",
    "EURGBP=X", "EURJPY=X", "EURCHF=X", "EURAUD=X", "EURCAD=X", "EURNZD=X",
    "GBPJPY=X", "GBPCHF=X", "GBPAUD=X", "GBPCAD=X", "GBPNZD=X",
    "AUDJPY=X", "AUDCHF=X", "AUDCAD=X", "AUDNZD=X",
    "CADJPY=X", "CADCHF=X", "NZDJPY=X", "NZDCHF=X", "CHFJPY=X"
]

INDICES_CRYPTO = ["NQ=F", "^GDAXI", "BTC-USD"]
ALL_SYMBOLS = FOREX + INDICES_CRYPTO

TIMEFRAMES = {
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d"
}

SESSIONS = {
    "Asian": (time(0, 0), time(9, 0)),
    "London": (time(7, 0), time(16, 0)),
    "NewYork": (time(12, 0), time(21, 0))
}

MIN_CONFIDENCE = 2.0
# ====================================================

def is_in_session():
    now = datetime.now(timezone.utc).time()
    for name, (start, end) in SESSIONS.items():
        if start <= now <= end:
            return True, name
    return False, None

def get_data(symbol, interval="15m", period="5d"):
    try:
        df = yf.download(symbol, interval=interval, period=period, progress=False, auto_adjust=True)
        if df.empty:
            return None
        return df.dropna()
    except Exception:
        return None

def find_swings(df, window=4):
    highs = df['High'].values
    lows = df['Low'].values
    swing_highs, swing_lows = [], []
    for i in range(window, len(df) - window):
        if highs[i] == max(highs[i-window:i+window+1]):
            swing_highs.append((i, highs[i]))
        if lows[i] == min(lows[i-window:i+window+1]):
            swing_lows.append((i, lows[i]))
    return swing_highs, swing_lows

def detect_liquidity_sweep(df):
    if len(df) < 30:
        return 0
    swing_highs, swing_lows = find_swings(df, 3)
    score = 0
    if len(swing_highs) >= 2:
        last, prev = swing_highs[-1][1], swing_highs[-2][1]
        if abs(last - prev) / prev < 0.0018:
            if df['High'].iloc[-1] > last and df['Close'].iloc[-1] < last:
                score -= 1
    if len(swing_lows) >= 2:
        last, prev = swing_lows[-1][1], swing_lows[-2][1]
        if abs(last - prev) / prev < 0.0018:
            if df['Low'].iloc[-1] < last and df['Close'].iloc[-1] > last:
                score += 1
    return score

def detect_lower_high_structure(df):
    if len(df) < 40:
        return 0
    swing_highs, swing_lows = find_swings(df, 4)
    score = 0
    if len(swing_highs) >= 2 and len(swing_lows) >= 1:
        if swing_highs[-1][1] < swing_highs[-2][1]:
            recent_low = swing_lows[-1][1]
            if df['Close'].iloc[-1] < recent_low:
                score -= 1
            elif df['Low'].iloc[-5:].min() >= recent_low * 0.997:
                score += 1
    return score

def detect_double_top_bottom(df):
    if len(df) < 50:
        return 0
    swing_highs, swing_lows = find_swings(df, 5)
    score = 0
    if len(swing_highs) >= 2:
        h1, h2 = swing_highs[-2][1], swing_highs[-1][1]
        if abs(h1 - h2) / h1 < 0.0025:
            idx1, idx2 = swing_highs[-2][0], swing_highs[-1][0]
            neckline = df['Low'].iloc[idx1:idx2+1].min()
            if df['Close'].iloc[-1] < neckline:
                score -= 1
    if len(swing_lows) >= 2:
        l1, l2 = swing_lows[-2][1], swing_lows[-1][1]
        if abs(l1 - l2) / l1 < 0.0025:
            idx1, idx2 = swing_lows[-2][0], swing_lows[-1][0]
            neckline = df['High'].iloc[idx1:idx2+1].max()
            if df['Close'].iloc[-1] > neckline:
                score += 1
    return score

def analyze_symbol(symbol):
    total = 0
    details = []
    weights = {"5m": 0.4, "15m": 0.7, "1h": 1.0, "4h": 1.3, "1d": 1.6}
    
    for tf_name, tf in TIMEFRAMES.items():
        period = "7d" if tf in ["5m", "15m"] else "90d"
        df = get_data(symbol, tf, period)
        if df is None or len(df) < 30:
            continue
        s1 = detect_liquidity_sweep(df)
        s2 = detect_lower_high_structure(df)
        s3 = detect_double_top_bottom(df)
        tf_score = (s1 + s2 + s3) * weights[tf_name]
        total += tf_score
        if s1 + s2 + s3 != 0:
            details.append(f"{tf_name}:{s1+s2+s3:+}")
    
    final = np.clip(total / 2.8, -3, 3)
    return round(final, 1), details

async def send_alert(context, symbol, score, details, session):
    direction = "LONG 🟢" if score > 0 else "SHORT 🔴"
    text = (
        f"**Signal Bot** — {datetime.now(timezone.utc).strftime('%a %d %b %H:%M')} UTC\n"
        f"Pair: `{symbol}`\n"
        f"Session: **{session}**\n"
        f"Direction: **{direction}**\n"
        f"Confidence: **{score:+.1f}** / 3\n\n"
        f"Confluence: {', '.join(details) if details else 'Multi-TF'}\n"
        f"Strategies: Liquidity + Structure + Double Top/Bottom"
    )
    await context.bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown")

async def scan_markets(context: ContextTypes.DEFAULT_TYPE):
    global CHAT_ID
    if not CHAT_ID:
        return
    in_session, session = is_in_session()
    if not in_session:
        return
    
    print(f"[{datetime.now(timezone.utc)}] Scanning... Session: {session}")
    for symbol in ALL_SYMBOLS:
        try:
            score, details = analyze_symbol(symbol)
            if abs(score) >= MIN_CONFIDENCE:
                await send_alert(context, symbol, score, details, session)
                await asyncio.sleep(1.2)
        except Exception as e:
            print(f"Error {symbol}: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CHAT_ID
    CHAT_ID = update.effective_chat.id
    await update.message.reply_text(
        "✅ Bot activated!\n\n"
        "Watching all majors + crosses + NQ + GER30 + BTC\n"
        "Sessions: Asian / London / New York\n"
        "Only high-confidence signals (≥ 2.0) will be sent."
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    in_session, session = is_in_session()
    await update.message.reply_text(
        f"Bot is online.\n"
        f"Current session: {session if in_session else 'Outside sessions'}\n"
        f"Symbols: {len(ALL_SYMBOLS)}"
    )

def main():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN environment variable is missing!")
        return
    
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    
    # Scan every 5 minutes
    app.job_queue.run_repeating(scan_markets, interval=300, first=15)
    
    print("Bot started successfully on Railway...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
