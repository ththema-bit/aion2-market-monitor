#!/usr/bin/env python3
"""
Aion 2 Market Query Tool
Usage:
  python3 market_query.py overview        — Übersicht: Top Performer + Signale
  python3 market_query.py item "Name"     — Details + Preisverlauf zu einem Item
  python3 market_query.py signals         — Nur Buy/Sell Signale
  python3 market_query.py top             — Top 20 nach Volumen
"""

import sqlite3
import sys
import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "market.db"

def get_db():
    if not DB_PATH.exists():
        print("❌ DB nicht gefunden. Monitor läuft noch nicht?")
        sys.exit(1)
    return sqlite3.connect(DB_PATH)

def latest_where():
    """Subquery für letzten Snapshot pro Item"""
    return "s.id IN (SELECT MAX(id) FROM snapshots GROUP BY item_key)"

def overview():
    con = get_db()
    last = con.execute("SELECT MAX(ts) FROM snapshots").fetchone()[0]
    print(f"📊 Aion 2 Markt-Übersicht | Stand: {last[:16]} UTC\n")

    rows = con.execute(f"""
        SELECT i.name, s.price, s.change_pct, s.units_sold, s.listings
        FROM snapshots s JOIN items i ON s.item_key = i.item_key
        WHERE {latest_where()}
          AND s.units_sold > 20 AND s.price > 500
        ORDER BY s.units_sold DESC LIMIT 15
    """).fetchall()

    print("🔥 TOP PERFORMER (Volumen):")
    for name, price, chg, sold, listings in rows:
        arrow = "📈" if (chg or 0) > 0 else "📉"
        print(f"  • {name}: {price:,.0f} Kinah {arrow}{chg or 0:+.1f}% | {sold} sold | {listings} listings")

    buys = con.execute(f"""
        SELECT i.name, s.price, s.change_pct, s.units_sold
        FROM snapshots s JOIN items i ON s.item_key = i.item_key
        WHERE {latest_where()}
          AND s.units_sold >= 10 AND s.change_pct <= -15
        ORDER BY s.change_pct ASC LIMIT 8
    """).fetchall()

    print(f"\n🟢 KAUFSIGNALE ({len(buys)}):")
    if buys:
        for name, price, chg, sold in buys:
            print(f"  • {name}: {price:,.0f} Kinah ({chg:+.1f}%) — {sold} sold/24h")
    else:
        print("  Keine klaren Kaufsignale gerade.")

    sells = con.execute(f"""
        SELECT i.name, s.price, s.change_pct, s.units_sold
        FROM snapshots s JOIN items i ON s.item_key = i.item_key
        WHERE {latest_where()}
          AND s.units_sold >= 10 AND s.change_pct >= 25
        ORDER BY s.change_pct DESC LIMIT 8
    """).fetchall()

    print(f"\n🔴 VERKAUFSSIGNALE ({len(sells)}):")
    if sells:
        for name, price, chg, sold in sells:
            print(f"  • {name}: {price:,.0f} Kinah (+{chg:.1f}%) — {sold} sold/24h → Take Profit!")
    else:
        print("  Keine klaren Verkaufssignale gerade.")

    con.close()

def item_detail(name):
    con = get_db()
    rows = con.execute(
        "SELECT item_key, name FROM items WHERE LOWER(name) LIKE LOWER(?) LIMIT 5",
        (f"%{name}%",)
    ).fetchall()

    if not rows:
        print(f"❌ Item '{name}' nicht gefunden.")
        return

    key, full_name = rows[0]
    print(f"📦 {full_name}\n")

    latest = con.execute("""
        SELECT price, change_pct, listings, units_sold, sold_avg, ts
        FROM snapshots WHERE item_key=? ORDER BY id DESC LIMIT 1
    """, (key,)).fetchone()

    if latest:
        price, chg, listings, sold, avg, ts = latest
        arrow = "📈" if (chg or 0) > 0 else "📉"
        print(f"  Preis:    {price:,.0f} Kinah  {arrow}{chg or 0:+.1f}%")
        print(f"  Listings: {listings}")
        print(f"  Verkauft: {sold or '?'} /24h | Ø Verkaufspreis: {avg:,.0f} Kinah" if avg else f"  Verkauft: {sold or '?'} /24h")
        print(f"  Stand:    {ts[:16]} UTC")

    history = con.execute("""
        SELECT ts, price FROM snapshots
        WHERE item_key=? AND ts > datetime('now', '-24 hours')
        ORDER BY id ASC
    """, (key,)).fetchall()

    if len(history) >= 2:
        prices = [r[1] for r in history if r[1]]
        change_24h = ((prices[-1] - prices[0]) / prices[0]) * 100
        print(f"\n  📈 24h Trend: {prices[0]:,.0f} → {prices[-1]:,.0f} Kinah ({change_24h:+.1f}%)")
        print(f"  Min: {min(prices):,.0f} | Max: {max(prices):,.0f} | Snapshots: {len(prices)}")

        if change_24h <= -20:
            print(f"\n  💡 Einschätzung: KAUFEN — Preis deutlich gefallen")
        elif change_24h >= 25:
            print(f"\n  💡 Einschätzung: VERKAUFEN — Preis gepumpt, Take Profit erwägen")
        elif -5 <= change_24h <= 5:
            print(f"\n  💡 Einschätzung: STABIL — kein klares Signal")
        else:
            print(f"\n  💡 Einschätzung: BEOBACHTEN")
    else:
        print(f"\n  ⚠️ Noch wenig Daten — Monitor läuft erst kurz. In 1-2h mehr History.")

    con.close()

def signals_only():
    con = get_db()
    last = con.execute("SELECT MAX(ts) FROM snapshots").fetchone()[0]
    print(f"🚨 Aktuelle Trading-Signale | {last[:16]} UTC\n")

    sigs = con.execute(f"""
        SELECT i.name, s.price, s.change_pct, s.units_sold,
               CASE WHEN s.change_pct <= -15 THEN 'BUY' ELSE 'SELL' END as signal
        FROM snapshots s JOIN items i ON s.item_key = i.item_key
        WHERE {latest_where()}
          AND s.units_sold >= 10
          AND (s.change_pct <= -15 OR s.change_pct >= 25)
        ORDER BY ABS(s.change_pct) DESC
    """).fetchall()

    if not sigs:
        print("  😴 Keine klaren Signale. Markt ist ruhig.")
    for name, price, chg, sold, sig in sigs:
        emoji = "🟢" if sig == "BUY" else "🔴"
        print(f"  {emoji} {sig}: {name} — {price:,.0f} Kinah ({chg:+.1f}%) | {sold} sold")

    con.close()

def top_items():
    con = get_db()
    rows = con.execute(f"""
        SELECT i.name, i.section, s.price, s.change_pct, s.units_sold
        FROM snapshots s JOIN items i ON s.item_key = i.item_key
        WHERE {latest_where()} AND s.units_sold IS NOT NULL
        ORDER BY s.units_sold DESC LIMIT 20
    """).fetchall()

    print("📊 Top 20 nach Handelsvolumen:\n")
    for i, (name, section, price, chg, sold) in enumerate(rows, 1):
        arrow = "📈" if (chg or 0) > 0 else "📉"
        print(f"  {i:2}. {name[:42]:<42} {price:>10,.0f} Kinah  {arrow}{chg or 0:+.1f}%  {sold:>8} sold")

    con.close()

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "overview"
    if cmd == "overview":
        overview()
    elif cmd == "item" and len(sys.argv) > 2:
        item_detail(" ".join(sys.argv[2:]))
    elif cmd == "signals":
        signals_only()
    elif cmd == "top":
        top_items()
    else:
        print(__doc__)
