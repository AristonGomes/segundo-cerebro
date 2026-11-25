#!/bin/bash
echo "Iniciando a API (FastAPI) em background..."
python -m uvicorn api_webhook:app --host 0.0.0.0 --port 8000 &

echo "Iniciando o Bot do Telegram..."
python telegram_bot.py
