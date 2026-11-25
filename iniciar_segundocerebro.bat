@echo off
title Segundo Cerebro - Bot & API
cd /d "%~dp0"

echo ===================================================
echo      Iniciando Segundo Cerebro Localmente
echo ===================================================
echo.

:: Inicia a API FastAPI minimizada
start "API SegundoCerebro" /min "%~dp0.venv\Scripts\python.exe" -m uvicorn api_webhook:app --host 127.0.0.1 --port 8000

echo [1/2] API iniciada em background (porta 8000)...
echo [2/2] Conectando ao bot do Telegram...
echo.
echo ===================================================
echo  Tudo pronto! Envie mensagens no Telegram.
echo  Para encerrar o sistema, basta fechar esta janela.
echo ===================================================
echo.

"%~dp0.venv\Scripts\python.exe" telegram_bot.py

:: Quando o bot fechar, encerra tambem a API
taskkill /FI "WINDOWTITLE eq API SegundoCerebro*" /F >nul 2>&1
