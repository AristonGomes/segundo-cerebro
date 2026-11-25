"""
config.py
---------
Configuracoes centrais do SegundoCerebro.
Carrega variaveis sensiveis do arquivo .env (nunca commite .env no repositorio).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Carrega o .env da raiz do projeto
load_dotenv(Path(__file__).parent / ".env")

# ------------------------------------------------------------------
# Gemini
# ------------------------------------------------------------------
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

# Modelo padrao. Pode ser sobrescrito no .env.
# Opcoes comuns: "gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash"
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
