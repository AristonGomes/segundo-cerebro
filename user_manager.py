"""
user_manager.py
---------------
Gerenciador Multi-Usuario (Multi-Cofre) do SegundoCerebro.

Permite que o mesmo bot do Telegram (`@Obsidian-bot`) atenda multiplos usuarios,
roteando cada pessoa automaticamente pelo seu `user_id` do Telegram para:
- Seu proprio Google Drive (via `APPS_SCRIPT_URL` exclusivo daquele usuario)
- Seu proprio Indice Mestre (`_index.json` e `00_Indice_MOC.md` isolados)
- Seu proprio contexto de tags e notas no Gemini (`NoteProcessor` dedicado)

Formas de configuracao suportadas (no `.env` ou Environment Variables da Vercel):
1. Variaveis numeradas simples (Recomendado na Vercel):
   - `USER_1_ID=111111111`
   - `USER_1_NAME=Ariston`
   - `USER_1_APPS_SCRIPT_URL=https://script.google.com/...` (ou usa `APPS_SCRIPT_URL` padrao)
   - `USER_2_ID=222222222`
   - `USER_2_NAME=Convidado`
   - `USER_2_APPS_SCRIPT_URL=https://script.google.com/...`
2. JSON unico opcional (`USERS_CONFIG_JSON`):
   - `{"111111111": {"name": "Ariston", "app_script_url": "..."}, ...}`
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from index_manager import IndexManager
from note_processor import NoteProcessor

log = logging.getLogger(__name__)


@dataclass
class UserVaultContext:
    user_id: str
    name: str
    app_script_url: str
    vault_subdir: str
    index_manager: IndexManager
    processor: NoteProcessor


class MultiUserManager:
    """Roteia cada `user_id` do Telegram para seu cofre, Indice MOC e Google Drive isolados."""

    def __init__(self) -> None:
        self._contexts: dict[str, UserVaultContext] = {}

    @staticmethod
    def load_configured_users() -> dict[str, dict[str, str]]:
        """
        Le os usuarios configurados nas variaveis de ambiente (`USER_1_ID`..`USER_20_ID`
        e/ou `USERS_CONFIG_JSON`).
        """
        users: dict[str, dict[str, str]] = {}
        default_script_url = os.getenv("APPS_SCRIPT_URL", "").strip()

        # 1. Le JSON opcional se existir
        raw_json = os.getenv("USERS_CONFIG_JSON", "").strip()
        if raw_json:
            try:
                parsed = json.loads(raw_json)
                if isinstance(parsed, dict):
                    for uid, cfg in parsed.items():
                        if isinstance(cfg, dict):
                            users[str(uid).strip()] = {
                                "name": str(cfg.get("name", f"User_{uid}")),
                                "app_script_url": str(cfg.get("app_script_url", "")).strip(),
                            }
            except Exception as exc:
                log.warning("USERS_CONFIG_JSON invalido: %s", exc)

        # 2. Le variaveis numeradas USER_1_ID ate USER_20_ID
        for i in range(1, 21):
            uid = os.getenv(f"USER_{i}_ID", "").strip()
            if not uid:
                continue
            name = os.getenv(f"USER_{i}_NAME", f"Usuario_{i}").strip()
            script_url = os.getenv(f"USER_{i}_APPS_SCRIPT_URL", "").strip()
            # Para o USER_1 (dono), se USER_1_APPS_SCRIPT_URL nao for repetido, usa APPS_SCRIPT_URL padrao
            if not script_url and i == 1:
                script_url = default_script_url
            users[uid] = {
                "name": name,
                "app_script_url": script_url,
            }

        return users

    def is_user_authorized(self, user_id: int | str | None) -> bool:
        """
        Verifica se o `user_id` tem permissao para usar o bot:
        - Se `USER_1_ID` estiver definido, opera em modo Whitelist Estrito (apenas IDs
          cadastrados em `USER_X_ID` ou `USERS_CONFIG_JSON` sao aceitos).
        - Se `USER_1_ID` nao estiver definido ainda, permite o uso no cofre padrao
          para o dono nao ficar bloqueado, enquanto roteia `USER_2_ID`+ para seus cofres.
        """
        if user_id is None:
            return True

        uid_str = str(user_id).strip()
        configured = self.load_configured_users()

        if uid_str in configured:
            return True

        # Se USER_1_ID foi configurado explicitamente, ativa bloqueio para desconhecidos
        strict_whitelist = bool(os.getenv("USER_1_ID", "").strip() or os.getenv("USERS_CONFIG_JSON", "").strip())
        return not strict_whitelist

    def get_context(self, user_id: int | str | None = None, user_name: str = "") -> UserVaultContext:
        """
        Retorna (ou cria em cache) o contexto isolado (`NoteProcessor` + `IndexManager`
        + `app_script_url`) correspondente ao `user_id` informado.
        """
        configured = self.load_configured_users()
        default_script_url = os.getenv("APPS_SCRIPT_URL", "").strip()

        uid_str = str(user_id).strip() if user_id is not None else "default"

        if uid_str in configured:
            cfg = configured[uid_str]
            app_script_url = cfg["app_script_url"]
            name = cfg["name"] or user_name or uid_str
            # Se for o unico usuario ou USER_1 usando a URL padrao, mantem a pasta padrao
            if app_script_url == default_script_url and uid_str == os.getenv("USER_1_ID", "").strip():
                vault_subdir = "obsidian_vault"
                cache_key = "default"
            else:
                vault_subdir = f"obsidian_vault/user_{uid_str}"
                cache_key = f"user_{uid_str}"
        else:
            # Fallback para o cofre principal (Ariston)
            app_script_url = default_script_url
            name = user_name or "Principal"
            vault_subdir = "obsidian_vault"
            cache_key = "default"

        # Verifica se ja existe em cache e se a URL continua igual
        existing = self._contexts.get(cache_key)
        if existing is not None and existing.app_script_url == app_script_url:
            return existing

        idx = IndexManager(vault_dir=vault_subdir, app_script_url=app_script_url)
        processor = NoteProcessor(index_manager=idx)
        ctx = UserVaultContext(
            user_id=uid_str,
            name=name,
            app_script_url=app_script_url,
            vault_subdir=vault_subdir,
            index_manager=idx,
            processor=processor,
        )
        self._contexts[cache_key] = ctx
        log.info(
            "Contexto carregado para usuario=%s (%s) | vault=%s | notas=%d",
            name,
            uid_str,
            vault_subdir,
            len(idx.get_all_notes()),
        )
        return ctx
