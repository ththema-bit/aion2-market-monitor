#!/usr/bin/env python3
"""
Aion 2 Market Monitor
Polls aion2.exchange API every 5 minutes, logs price history to SQLite,
analyzes trends, and sends buy/sell signals + top performers via Telegram.

Optional AI analysis via any OpenAI-compatible endpoint (LLM_URL, LLM_MODEL).
"""

import requests
import sqlite3
import json
import time
import datetime
import os
import sys
import logging
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID")
OLLAMA_ENABLED     = bool(os.environ.get("LLM_URL"))  # AI summary only if LLM_URL is set
# Any OpenAI-compatible endpoint (vLLM, Ollama /v1, ...)
OLLAMA_URL         = os.environ.get("LLM_URL", "")
OLLAMA_MODEL       = os.environ.get("LLM_MODEL", "qwen3.6-27b")
POLL_INTERVAL_SEC  = int(os.environ.get("POLL_INTERVAL", "300"))  # 5 min
DB_PATH            = Path(__file__).parent / "market.db"
LOG_PATH           = Path(__file__).parent / "monitor.log"
API_BASE           = "https://aion2.exchange/api"

# Items to always track (high-volume, useful for trading) — auto-expanded on startup
TRACKED_ITEMS_INIT = []  # empty = track ALL items with sales data

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("aion2")

# ── DB Setup ──────────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS items (
            item_key   INTEGER PRIMARY KEY,
            name       TEXT,
            category   TEXT,
            section    TEXT
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            item_key    INTEGER,
            ts          TEXT,
            price       REAL,
            listings    INTEGER,
            change_pct  REAL,
            units_sold  INTEGER,
            sold_avg    REAL,
            captured_at TEXT
        );
        CREATE TABLE IF NOT EXISTS signals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT,
            item_key    INTEGER,
            name        TEXT,
            signal      TEXT,
            reason      TEXT,
            price       REAL
        );
        CREATE INDEX IF NOT EXISTS idx_snap_item ON snapshots(item_key, ts);
    """)
    con.commit()
    return con

# ── API ───────────────────────────────────────────────────────────────────────
def fetch_all_items():
    """Fetch all market items — paginate in chunks of 200 (API max)"""
    all_items = []
    try:
        # First page
        r = requests.get(f"{API_BASE}/items", params={
            "limit": 200, "sort": "units_sold", "order": "desc", "offset": 0
        }, timeout=20)
        r.raise_for_status()
        data = r.json()
        all_items.extend(data.get("items", []))
        total = data.get("total", 0)
        # More pages?
        offset = 200
        while offset < total and offset < 600:  # cap at 600
            r = requests.get(f"{API_BASE}/items", params={
                "limit": 200, "sort": "units_sold", "order": "desc", "offset": offset
            }, timeout=20)
            r.raise_for_status()
            page = r.json().get("items", [])
            if not page:
                break
            all_items.extend(page)
            offset += 200
            time.sleep(1)  # polite
    except Exception as e:
        log.error(f"fetch_all_items error: {e}")
    return all_items

def fetch_item_depth(item_key, units=50):
    """Fetch hourly price depth/history for one item"""
    try:
        r = requests.get(f"{API_BASE}/items/{item_key}/depth",
                         params={"units": units, "bucket": "1h"}, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.warning(f"depth fetch error for {item_key}: {e}")
    return None

# ── DB Write ──────────────────────────────────────────────────────────────────
def upsert_item(con, item):
    con.execute("""
        INSERT OR REPLACE INTO items (item_key, name, category, section)
        VALUES (?, ?, ?, ?)
    """, (
        item["item_key"], item["name"],
        item.get("category", {}).get("name", ""),
        item.get("category", {}).get("section_name", ""),
    ))

def insert_snapshot(con, item):
    now = datetime.datetime.utcnow().isoformat()
    con.execute("""
        INSERT INTO snapshots (item_key, ts, price, listings, change_pct, units_sold, sold_avg, captured_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        item["item_key"], now,
        item.get("price"), item.get("listings"),
        item.get("change_pct"), item.get("units_sold"),
        item.get("sold_avg_price"),
        item.get("captured_at"),
    ))

# ── Analysis ──────────────────────────────────────────────────────────────────
def get_price_history(con, item_key, hours=24):
    """Get last N hours of snapshots for an item"""
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(hours=hours)).isoformat()
    rows = con.execute("""
        SELECT ts, price, listings, change_pct, units_sold
        FROM snapshots
        WHERE item_key=? AND ts>?
        ORDER BY ts ASC
    """, (item_key, cutoff)).fetchall()
    return rows

def analyze_trends(con, items):
    """
    Simple trend analysis — returns list of signals:
    BUY: price dropped >15% in last cycle AND has real sales volume
    SELL: price pumped >30% in last cycle (take profit)
    HOT: top movers with real volume
    """
    signals = []
    hot_items = []

    for item in items:
        sold = item.get("units_sold") or 0
        price = item.get("price") or 0
        change = item.get("change_pct") or 0
        name = item["name"]
        key = item["item_key"]

        # Only care about items with real sales (not ghost listings)
        if sold < 10 or price < 100:
            continue

        history = get_price_history(con, key, hours=6)

        # Need at least 2 data points
        if len(history) < 2:
            if change <= -15:
                signals.append({
                    "item_key": key, "name": name,
                    "signal": "BUY",
                    "reason": f"Preis -${abs(change):.1f}% in 24h, {sold} verkauft",
                    "price": price,
                })
            elif change >= 30:
                signals.append({
                    "item_key": key, "name": name,
                    "signal": "SELL",
                    "reason": f"Preis +{change:.1f}% in 24h, {sold} verkauft → Take Profit",
                    "price": price,
                })
        else:
            prices = [r[1] for r in history if r[1]]
            if len(prices) >= 2:
                recent_change = ((prices[-1] - prices[0]) / prices[0]) * 100
                if recent_change <= -20:
                    signals.append({
                        "item_key": key, "name": name,
                        "signal": "BUY",
                        "reason": f"Preis -{abs(recent_change):.1f}% in letzten 6h, {sold} sold/24h",
                        "price": price,
                    })
                elif recent_change >= 25:
                    signals.append({
                        "item_key": key, "name": name,
                        "signal": "SELL",
                        "reason": f"Preis +{recent_change:.1f}% in letzten 6h → Take Profit",
                        "price": price,
                    })

        # Hot items: high volume + positive trend
        if sold > 100 and change > 5:
            hot_items.append((sold, change, name, price, key))

    hot_items.sort(reverse=True)
    return signals, hot_items[:10]

def get_top_performers(con, items):
    """Top 10 by volume x price momentum"""
    tradeable = [
        i for i in items
        if (i.get("units_sold") or 0) >= 20
        and (i.get("price") or 0) > 500
    ]
    tradeable.sort(key=lambda x: (x.get("units_sold") or 0) * max(x.get("change_pct") or 0, 1), reverse=True)
    return tradeable[:10]

# ── Ollama AI Analysis ────────────────────────────────────────────────────────
def ai_analyze(signals, top_performers, hot_items):
    """Ask local Qwen3 to give a trading summary & recommendation"""
    if not OLLAMA_ENABLED:
        return None
    if not signals and not top_performers:
        return None

    # Build context
    sig_text = "\n".join([
        f"- {s['signal']} {s['name']}: {s['reason']} (Preis: {s['price']:,.0f} Kinah)"
        for s in signals[:8]
    ])
    top_text = "\n".join([
        f"- {i['name']}: {i['price']:,.0f} Kinah | {i.get('units_sold',0)} sold | {i.get('change_pct',0):+.1f}%"
        for i in top_performers[:5]
    ])

    prompt = f"""Du bist ein Aion 2 Markt-Analyst. Analysiere diese Marktdaten und gib kurze, klare Trading-Empfehlungen auf Deutsch.

SIGNALE JETZT:
{sig_text if sig_text else "Keine klaren Signale aktuell."}

TOP PERFORMER (Volumen + Trend):
{top_text if top_text else "Keine Daten."}

Gib eine kurze Zusammenfassung (max 5 Sätze) mit:
1. Was jetzt kaufenswert ist und warum
2. Was man verkaufen sollte
3. Welches Item gerade am heißesten läuft
Kein Markdown, nur Klartext."""

    try:
        r = requests.post(f"{OLLAMA_URL}/chat/completions", json={
            "model": OLLAMA_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 400,
        }, timeout=180)
        if r.status_code == 200:
            content = (r.json()["choices"][0]["message"].get("content") or "").strip()
            if content:
                log.info(f"AI summary generated ({len(content)} chars)")
            else:
                log.warning("Ollama returned empty content")
            return content or None
    except Exception as e:
        log.warning(f"Ollama AI analysis failed: {e}")
    return None

# ── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram(text, parse_mode="HTML"):
    if not TELEGRAM_BOT_TOKEN:
        log.warning("No Telegram token configured!")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": parse_mode},
            timeout=10,
        )
        if r.status_code != 200:
            log.error(f"Telegram error: {r.text}")
    except Exception as e:
        log.error(f"Telegram send failed: {e}")

def format_message(signals, top_performers, ai_summary, cycle_num):
    now = datetime.datetime.utcnow().strftime("%H:%M UTC")
    lines = [f"📊 <b>Aion 2 Market Report</b> — {now} (#{cycle_num})\n"]

    if signals:
        buys  = [s for s in signals if s["signal"] == "BUY"]
        sells = [s for s in signals if s["signal"] == "SELL"]
        if buys:
            lines.append("🟢 <b>KAUFEN:</b>")
            for s in buys[:4]:
                lines.append(f"  • <b>{s['name']}</b>: {s['price']:,.0f} Kinah\n    ↳ {s['reason']}")
        if sells:
            lines.append("\n🔴 <b>VERKAUFEN:</b>")
            for s in sells[:4]:
                lines.append(f"  • <b>{s['name']}</b>: {s['price']:,.0f} Kinah\n    ↳ {s['reason']}")
    else:
        lines.append("😴 Keine klaren Kauf/Verkauf-Signale gerade.")

    if top_performers:
        lines.append("\n🔥 <b>Top Performer:</b>")
        for i, item in enumerate(top_performers[:5], 1):
            chg = item.get("change_pct", 0) or 0
            arrow = "📈" if chg > 0 else "📉"
            lines.append(f"  {i}. {item['name']}: {item['price']:,.0f} Kinah {arrow}{chg:+.1f}% | {item.get('units_sold',0)} sold")

    if ai_summary:
        lines.append(f"\n🤖 <b>AI-Analyse:</b>\n{ai_summary}")

    lines.append(f"\n<i>Nächstes Update in 5 min · aion2.exchange</i>")
    return "\n".join(lines)

# ── Signal Dedup ─────────────────────────────────────────────────────────────
def signals_fingerprint(signals):
    """Set von (item_key, signal) als Fingerprint"""
    return {(s["item_key"], s["signal"]) for s in signals}

def top_prices_changed(new_top, last_top, threshold=0.10):
    """True wenn Preise der Top-Items >10% gedriftet sind"""
    if not last_top:
        return True
    last_map = {i["item_key"]: i.get("price", 0) for i in last_top}
    for item in new_top:
        lp = last_map.get(item["item_key"])
        if lp and lp > 0:
            if abs(item.get("price", 0) - lp) / lp >= threshold:
                return True
    return False

# ── Main Loop ─────────────────────────────────────────────────────────────────
def main():
    log.info("🚀 Aion 2 Market Monitor starting...")
    con = init_db()
    cycle             = 0
    last_report_time  = 0
    last_ai_time      = 0
    last_signal_fp    = set()   # fingerprint der zuletzt gesendeten Signale
    last_top          = []
    REPORT_INTERVAL   = 3600    # Stunden-Report (1x/h)
    AI_INTERVAL       = 3600    # AI-Summary max 1x/h

    send_telegram("🟢 <b>Aion 2 Market Monitor gestartet!</b>\nPolling alle 5 Min · Stunden-Report · Sofort-Alert bei neuen Signalen.")

    while True:
        cycle += 1
        cycle_start = time.time()
        log.info(f"=== Cycle {cycle} ===")

        items = fetch_all_items()
        if not items:
            log.warning("No items fetched, retrying next cycle")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        for item in items:
            upsert_item(con, item)
            insert_snapshot(con, item)
        con.commit()
        log.info(f"Stored {len(items)} items")

        signals, hot_items = analyze_trends(con, items)
        top_performers     = get_top_performers(con, items)
        now_ts             = time.time()

        # Neue Signale = Signale die noch nicht gesendet wurden
        all_signals  = [s for s in signals if s["signal"] in ("BUY", "SELL")]
        current_fp   = signals_fingerprint(all_signals)
        new_fp       = current_fp - last_signal_fp
        has_new      = bool(new_fp)

        # Nur neue Signale in die Message
        new_signals  = [s for s in all_signals if (s["item_key"], s["signal"]) in new_fp]

        hourly_due   = (now_ts - last_report_time) >= REPORT_INTERVAL
        ai_due       = (now_ts - last_ai_time) >= AI_INTERVAL
        price_shift  = top_prices_changed(top_performers, last_top)
        want_ai      = ai_due and (has_new or price_shift or hourly_due)

        should_send  = has_new or hourly_due

        if should_send:
            ai_summary = ai_analyze(new_signals or all_signals, top_performers, hot_items) if want_ai else None
            if ai_summary:
                last_ai_time = now_ts

            # Beim Stunden-Report alle aktuellen Signale zeigen, sonst nur neue
            display_signals = all_signals if hourly_due else new_signals
            msg = format_message(display_signals, top_performers, ai_summary, cycle)
            send_telegram(msg)

            last_report_time = now_ts
            last_signal_fp   = current_fp   # alle aktuellen als "bekannt" markieren
            last_top         = top_performers[:]
            log.info(f"Report sent: {len(new_signals)} new signals, hourly={hourly_due}, ai={'yes' if ai_summary else 'no'}")
        else:
            mins_left = int((REPORT_INTERVAL - (now_ts - last_report_time)) / 60)
            log.info(f"Keine neuen Signale. Nächster Report in ~{mins_left} min")

        elapsed = time.time() - cycle_start
        time.sleep(max(0, POLL_INTERVAL_SEC - elapsed))

if __name__ == "__main__":
    main()
