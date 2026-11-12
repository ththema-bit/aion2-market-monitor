# aion2-market-monitor

A small Python daemon that polls the public aion2.exchange market API for Aion 2 item prices,
stores snapshots in SQLite, derives simple buy/sell signals and top performers from the price
history, and can send a summary to Telegram. An optional OpenAI-compatible LLM endpoint can add a short
plain-text summary. This is an unofficial hobby tool and not affiliated with the game or the market site.

## Features

- Periodic polling (default every 5 minutes) of item prices and market depth
- SQLite history (`market.db`, created next to the script)
- Trend based buy/sell signals and a "top performers by volume and trend" list
- Telegram notifications (optional)
- LLM summary (optional, any OpenAI-compatible `/chat/completions` endpoint)
- `market_query.py` CLI to inspect the database (German output)

## Requirements

- Python 3.9+
- `pip install requests`

## Run

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC..."   # optional
export TELEGRAM_CHAT_ID="YOUR_CHAT_ID"      # optional
export LLM_URL="http://localhost:8010/v1"   # optional, enables the AI summary
export LLM_MODEL="your-model"
python3 aion2_monitor.py
```

Without a Telegram token the monitor still collects data and logs a warning when it would send a message.
`aion2-monitor.service` is an example systemd unit; copy your variables into `/etc/aion2-monitor.env`.

Query the data:

```bash
python3 market_query.py overview   # top performers and signals
python3 market_query.py item "Item name"
python3 market_query.py signals
python3 market_query.py top
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | empty | Telegram bot token |
| `TELEGRAM_CHAT_ID` | `YOUR_CHAT_ID` | Target chat |
| `LLM_URL` | empty | Base URL of an OpenAI-compatible API; empty disables AI summaries |
| `LLM_MODEL` | `qwen3.6-27b` | Model name sent to the endpoint |
| `POLL_INTERVAL` | `300` | Seconds between polls |

## Notes

The signals are naive heuristics, not financial advice. The market API is not documented here and may change.

## License

MIT
