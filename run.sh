#!/bin/bash
# Aion 2 Market Monitor launcher. Set the variables below in your environment or edit here.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# export TELEGRAM_BOT_TOKEN="..."        # optional, enables Telegram messages
# export TELEGRAM_CHAT_ID="YOUR_CHAT_ID"
# export LLM_URL="http://localhost:8010/v1"   # optional OpenAI-compatible endpoint for AI summaries
# export LLM_MODEL="your-model"
export POLL_INTERVAL="${POLL_INTERVAL:-300}"

cd "$SCRIPT_DIR"
exec python3 aion2_monitor.py
