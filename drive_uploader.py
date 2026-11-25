"""
drive_uploader.py
-----------------
Modulo para upload, atualizacao e leitura de arquivos no Google Drive via Apps Script Webhook.
Permite operar 100% stateless em ambientes Serverless (como Vercel).
"""

import logging
import requests

log = logging.getLogger(__name__)


def upload_note_to_drive(
    filename: str,
    content: str,
    app_script_url: str,
    overwrite: bool = False,
) -> str:
    """
    Envia um arquivo para o Google Drive via Apps Script.
    Se overwrite=True, atualiza o arquivo existente no lugar (evita duplicatas).
    """
    payload = {
        "action": "upsert" if overwrite else "create",
        "overwrite": overwrite,
        "filename": filename,
        "content": content,
    }

    response = requests.post(app_script_url, json=payload, timeout=30)
    response.raise_for_status()
    return response.text


def fetch_file_from_drive(filename: str, app_script_url: str) -> str | None:
    """
    Tenta ler o conteudo de um arquivo (ex: `_index.json`) do Google Drive via Apps Script.
    Retorna None caso o arquivo ainda nao exista ou o script antigo nao suporte leitura.
    """
    if not app_script_url:
        return None
    try:
        response = requests.post(
            app_script_url,
            json={"action": "read", "filename": filename},
            timeout=15,
        )
        if response.status_code == 200:
            text = response.text
            # O script antigo sempre retornava "Sucesso" para qualquer POST
            if text in ("Sucesso", "NAO_ENCONTRADO") or text.startswith("Criado:"):
                return None
            return text
    except Exception as exc:
        log.debug("Nao foi possivel ler %s do Drive: %s", filename, exc)
    return None
