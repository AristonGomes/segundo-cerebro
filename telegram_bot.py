"""
telegram_bot.py
---------------
Fases 5 a 10 do SegundoCerebro - Bot do Telegram Multimodal, Interativo e Multi-Usuario.
Compativel tanto com:
1. Modo Serverless / Webhook (Vercel via `POST /telegram/webhook` no `api_webhook.py`)
2. Modo Local / Polling (`iniciar_segundocerebro.bat` ou `python telegram_bot.py`)

Isolamento Multi-Usuario:
- Roteia automaticamente cada `user_id` do Telegram para seu proprio cofre,
  seu proprio Indice Mestre (`_index.json` / `00_Indice_MOC.md`) e seu proprio
  Google Drive (`USER_X_APPS_SCRIPT_URL`).
"""

import datetime
import logging
import os
import re
from pathlib import Path
from typing import Any

import telebot
from dotenv import load_dotenv

from drive_uploader import upload_note_to_drive
from index_manager import MOC_FILENAME, IndexManager, resolve_writable_vault_dir
from note_processor import NoteProcessor
from user_manager import MultiUserManager, UserVaultContext

# Carrega variaveis do .env
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
APPS_SCRIPT_URL = os.getenv("APPS_SCRIPT_URL", "")

log = logging.getLogger(__name__)

# Inicializa TeleBot com threaded=False para execucao sincrona na Vercel Serverless
bot = telebot.TeleBot(TELEGRAM_TOKEN or "000000:TOKEN_NAO_CONFIGURADO", threaded=False)

# Gerenciador Multi-Usuario
_user_manager = MultiUserManager()


def get_user_manager() -> MultiUserManager:
    return _user_manager


def get_services(
    user_id: int | str | None = None,
    user_name: str = "",
) -> tuple[NoteProcessor, IndexManager]:
    """Retorna `(NoteProcessor, IndexManager)` isolados para o `user_id` informado."""
    ctx = _user_manager.get_context(user_id=user_id, user_name=user_name)
    return ctx.processor, ctx.index_manager


def bind_services(processor: NoteProcessor, index_manager: IndexManager) -> None:
    """Mantido para compatibilidade com a inicializacao do FastAPI."""
    pass


def process_telegram_update(update_dict: dict[str, Any]) -> None:
    """Processa um payload de Webhook enviado pelos servidores do Telegram (usado na Vercel)."""
    update = telebot.types.Update.de_json(update_dict)
    if update:
        bot.process_new_updates([update])


def _get_authorized_context(message) -> UserVaultContext | None:
    """
    Verifica se o remetente esta autorizado e retorna seu `UserVaultContext` isolado.
    Se nao estiver autorizado, envia mensagem informando o ID do Telegram dele.
    """
    uid = message.from_user.id
    first_name = message.from_user.first_name or "Usuário"
    if not _user_manager.is_user_authorized(uid):
        bot.reply_to(
            message,
            f"🔒 *Acesso Restrito*\n\n"
            f"Olá, {first_name}! Este bot do Segundo Cérebro é privado.\n"
            f"🆔 Seu ID do Telegram é: `{uid}`\n\n"
            f"_Envie este número ao administrador para conectar o seu cofre do Obsidian._",
            parse_mode="Markdown",
        )
        return None
    return _user_manager.get_context(user_id=uid, user_name=first_name)


# ---------------------------------------------------------------------------
# Helpers de Salvamento, Indice MOC e Formatacao
# ---------------------------------------------------------------------------

def extract_title(markdown: str) -> str:
    match = re.search(r'titulo:\s*"(.*?)"', markdown)
    if match:
        title = re.sub(r'[\\/*?:"<>|]', "", match.group(1))
        return title.replace(" ", "_")[:60]
    return f"nota_telegram_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"


def extract_summary_and_tags(markdown: str) -> tuple[str, list[str]]:
    resumo_match = re.search(r"^>\s*(.+)$", markdown, re.MULTILINE)
    resumo = resumo_match.group(1).strip() if resumo_match else "Nota estruturada com sucesso."

    tags: list[str] = []
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", markdown, re.DOTALL)
    if fm_match:
        tags = [
            t.strip()
            for t in re.findall(r'^\s*-\s*"?([^"\n]+)"?', fm_match.group(1), re.MULTILINE)
            if t.strip()
        ]
    return resumo, tags


def save_note_and_update_moc(
    markdown_content: str,
    ctx: UserVaultContext | None = None,
) -> tuple[str, str]:
    """
    1. Salva a nota no vault isolado do usuario (`ctx.vault_subdir`).
    2. Registra a nota no Indice Mestre do usuario (`_index.json` e `00_Indice_MOC.md`).
    3. Envia a nota e o MOC atualizado para o Google Drive especifico do usuario (`ctx.app_script_url`).
    """
    if ctx is None:
        ctx = _user_manager.get_context()

    idx = ctx.index_manager
    vault_dir = resolve_writable_vault_dir(ctx.vault_subdir)

    base_filename = f"{extract_title(markdown_content)}.md"
    filepath = vault_dir / base_filename
    counter = 1
    while filepath.exists():
        filepath = vault_dir / f"{Path(base_filename).stem}_{counter}.md"
        counter += 1

    filename = filepath.name
    filepath.write_text(markdown_content, encoding="utf-8")

    # Registra no Indice Mestre do usuario e gera o MOC atualizado
    meta = IndexManager.parse_markdown_metadata(filename, markdown_content)
    idx.register_note(
        filename=filename,
        titulo=meta["titulo"],
        tags=meta["tags"],
        resumo=meta["resumo"],
        data_criacao=meta["data"],
        tarefas=meta["tarefas"],
        fonte=meta["fonte"],
        sync_drive=True,
    )
    moc_markdown = idx.generate_moc_markdown()

    # Envia nota + 00_Indice_MOC.md para o Google Drive do usuario
    app_script_url = ctx.app_script_url
    if app_script_url:
        upload_note_to_drive(filename, markdown_content, app_script_url, overwrite=False)
        try:
            upload_note_to_drive(MOC_FILENAME, moc_markdown, app_script_url, overwrite=True)
        except Exception as exc:
            log.warning("Aviso ao sincronizar MOC no Drive (%s): %s", ctx.name, exc)
        saved_msg = (
            f"✅ *Nota salva no Google Drive ({ctx.name}) + Índice MOC atualizado!*\n"
            f"📄 Arquivo: `{filename}`"
        )
    else:
        saved_msg = (
            f"✅ *Nota salva localmente ({ctx.name}) + Índice MOC atualizado!*\n"
            f"📄 Arquivo: `{filename}`"
        )

    return filename, saved_msg


def safe_edit_or_reply(chat_id: int, message_id: int, text: str) -> None:
    """Edita a mensagem de status com fallback sem Markdown caso haja caracteres especiais."""
    if len(text) > 4000:
        text = text[:3990] + "\n..."
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="Markdown",
        )
    except Exception:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
        )


def format_note_confirmation(saved_msg: str, markdown_content: str) -> str:
    resumo, tags = extract_summary_and_tags(markdown_content)
    tags_str = " ".join(f"`#{t}`" for t in tags) if tags else "`#nota`"
    return (
        f"{saved_msg}\n"
        f"🏷️ Tags: {tags_str}\n\n"
        f"📌 *Resumo:*\n_{resumo}_"
    )


# ---------------------------------------------------------------------------
# Comandos de Identificacao, Indice MOC, Busca e Revisao
# ---------------------------------------------------------------------------

@bot.message_handler(commands=["meuid", "id"])
def handle_my_id(message):
    uid = message.from_user.id
    first_name = message.from_user.first_name or "Usuário"
    authorized = _user_manager.is_user_authorized(uid)
    status_str = "✅ Autorizado e Ativo" if authorized else "🔒 Aguardando liberação"
    bot.reply_to(
        message,
        f"👤 *Identificação de Usuário*\n\n"
        f"• *Nome:* {first_name}\n"
        f"• *Seu ID do Telegram:* `{uid}`\n"
        f"• *Status do Cofre:* {status_str}",
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    ctx = _get_authorized_context(message)
    if ctx is None:
        return

    help_text = (
        f"🧠 *Segundo Cérebro — Olá, {ctx.name}!*\n"
        f"🆔 Seu ID: `{message.from_user.id}`\n\n"
        "*📥 Captura Inteligente (basta enviar):*\n"
        "• *Texto:* Estrutura nota com YAML, tags do seu índice e wikilinks.\n"
        "• *Áudio / Voz:* Transcreve e organiza seus pensamentos falados.\n"
        "• *Foto / Imagem:* Lê páginas de livros, quadros ou slides (OCR).\n"
        "• *Link (http...):* Extrai o artigo e cria uma Nota de Literatura.\n\n"
        "*🔍 Consulta & Índice Mestre (MOC):*\n"
        "• `? sua pergunta` ou `/perguntar ...` — Conversa com seu cofre\n"
        "• `/indice` ou `/moc` — Ver o Mapa de Conteúdo e sincronizar com o Drive\n"
        "• `/tags` — Listar todas as `#tags` do seu Segundo Cérebro\n"
        "• `/buscar <termo>` — Buscar notas por tag, título ou palavra\n"
        "• `/tarefas` — Ver todos os próximos passos pendentes (`- [ ]`)\n"
        "• `/resumo` — Gerar nota de Revisão Semanal dos seus estudos\n"
        "• `/meuid` — Ver seu ID numérico do Telegram"
    )
    bot.reply_to(message, help_text, parse_mode="Markdown")


@bot.message_handler(commands=["tags"])
def handle_tags(message):
    ctx = _get_authorized_context(message)
    if ctx is None:
        return
    try:
        idx = ctx.index_manager
        tags = idx.get_all_tags()
        total_notes = len(idx.get_all_notes())

        if not tags:
            bot.reply_to(message, "🏷️ Nenhuma tag catalogada no seu cofre ainda. Envie sua primeira nota!")
            return

        lines = [f"🏷️ *Índice de Tags — {ctx.name} ({len(tags)} tags em {total_notes} notas):*\n"]
        for tag, count in tags.items():
            plural = "nota" if count == 1 else "notas"
            lines.append(f"• `#{tag}` — *{count}* {plural}")
        lines.append("\n💡 _Use_ `/buscar nome-da-tag` _para ver as notas dessa tag._")
        bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")
    except Exception as exc:
        bot.reply_to(message, f"❌ Erro ao consultar tags: `{exc}`", parse_mode="Markdown")


@bot.message_handler(commands=["indice", "moc"])
def handle_moc(message):
    ctx = _get_authorized_context(message)
    if ctx is None:
        return

    status_msg = bot.reply_to(
        message,
        f"🗂️ Atualizando e sincronizando o Mapa de Conteúdo de *{ctx.name}* (`00_Indice_MOC.md`)...",
        parse_mode="Markdown",
    )
    try:
        idx = ctx.index_manager
        all_notes = idx.get_all_notes()
        tags = idx.get_all_tags()
        notes = all_notes[:8]
        moc_md = idx.generate_moc_markdown()

        drive_status = f"Salvo localmente em `{ctx.vault_subdir}/00_Indice_MOC.md`"
        if ctx.app_script_url and moc_md:
            upload_note_to_drive(MOC_FILENAME, moc_md, ctx.app_script_url, overwrite=True)
            drive_status = "Sincronizado no seu Google Drive (`00_Indice_MOC.md`)"

        top_tags = " ".join(f"`#{t}` ({c})" for t, c in list(tags.items())[:10]) or "_Nenhuma_"
        recent_list = "\n".join(
            f"• `{n['stem']}` *({n['data']})*" for n in notes
        ) or "_Nenhuma nota registrada ainda._"

        reply = (
            f"🧠 *Mapa de Conteúdo (MOC) — {ctx.name}*\n"
            f"📊 *{len(all_notes)}* notas catalogadas | *{len(tags)}* tags únicas\n"
            f"☁️ Status: {drive_status}\n\n"
            f"🏷️ *Principais Tags:*\n{top_tags}\n\n"
            f"📚 *Últimas Notas:*\n{recent_list}"
        )
        safe_edit_or_reply(message.chat.id, status_msg.message_id, reply)
    except Exception as exc:
        safe_edit_or_reply(message.chat.id, status_msg.message_id, f"❌ Erro ao gerar MOC: {exc}")


@bot.message_handler(commands=["buscar"])
def handle_search(message):
    ctx = _get_authorized_context(message)
    if ctx is None:
        return

    query = message.text.partition(" ")[2].strip()
    if not query:
        bot.reply_to(
            message,
            "🔍 Informe o termo de busca. Exemplo:\n`/buscar filosofia`",
            parse_mode="Markdown",
        )
        return

    try:
        notes = ctx.index_manager.search(query, limit=8)
        if not notes:
            bot.reply_to(message, f"📭 Nenhuma nota encontrada no seu cofre para `{query}`.", parse_mode="Markdown")
            return

        lines = [f"🔍 *Resultados para* `{query}` *({len(notes)} encontradas):*\n"]
        for n in notes:
            tags_fmt = " ".join(f"`#{t}`" for t in n.get("tags", []))
            lines.append(
                f"📄 *{n['titulo']}* (`{n['stem']}`)\n"
                f"   📅 {n['data']} | {tags_fmt}\n"
                f"   _{n.get('resumo', '')}_\n"
            )
        bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")
    except Exception as exc:
        bot.reply_to(message, f"❌ Erro na busca: `{exc}`", parse_mode="Markdown")


@bot.message_handler(commands=["tarefas"])
def handle_tasks(message):
    ctx = _get_authorized_context(message)
    if ctx is None:
        return
    try:
        pending = ctx.index_manager.get_pending_tasks()
        if not pending:
            bot.reply_to(message, "✅ Nenhuma tarefa pendente encontrada nas suas notas!")
            return

        lines = [f"✅ *Tarefas e Próximos Passos Pendentes ({ctx.name}):*\n"]
        for item in pending:
            lines.append(f"📌 *{item['titulo']}* (`{item['stem']}`):")
            for t in item["tarefas"]:
                lines.append(f"  ☐ {t}")
            lines.append("")
        bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")
    except Exception as exc:
        bot.reply_to(message, f"❌ Erro ao listar tarefas: `{exc}`", parse_mode="Markdown")


@bot.message_handler(commands=["resumo", "resumo_semana"])
def handle_weekly_digest(message):
    ctx = _get_authorized_context(message)
    if ctx is None:
        return

    msg_status = bot.reply_to(message, "⏳ Gerando sua Revisão Semanal com base nas suas notas recentes...")
    try:
        markdown_content = ctx.processor.generate_weekly_digest(ctx.index_manager, days=7)
        _, saved_msg = save_note_and_update_moc(markdown_content, ctx=ctx)
        confirmation = format_note_confirmation(saved_msg, markdown_content)
        safe_edit_or_reply(message.chat.id, msg_status.message_id, confirmation)
    except Exception as exc:
        safe_edit_or_reply(message.chat.id, msg_status.message_id, f"❌ Erro ao gerar resumo: {exc}")


@bot.message_handler(commands=["perguntar"])
def handle_ask_command(message):
    ctx = _get_authorized_context(message)
    if ctx is None:
        return

    question = message.text.partition(" ")[2].strip()
    if not question:
        bot.reply_to(
            message,
            "💬 Envie sua pergunta após o comando ou comece a mensagem com `?`.\n"
            "Exemplo: `? O que eu anotei sobre Santo Agostinho?`",
            parse_mode="Markdown",
        )
        return
    _run_vault_question(message, question, ctx)


def _run_vault_question(message, question: str, ctx: UserVaultContext) -> None:
    msg_status = bot.reply_to(message, f"🧠 Consultando o Segundo Cérebro de {ctx.name}...")
    try:
        answer = ctx.processor.ask_vault(question, ctx.index_manager)
        sources = [n["stem"] for n in ctx.index_manager.search(question, limit=5)]
        sources_str = (
            "\n\n📚 *Notas consultadas:* " + ", ".join(f"`[[{s}]]`" for s in sources)
            if sources
            else ""
        )
        safe_edit_or_reply(message.chat.id, msg_status.message_id, f"{answer}{sources_str}")
    except Exception as exc:
        safe_edit_or_reply(message.chat.id, msg_status.message_id, f"❌ Erro ao consultar o cofre: {exc}")


# ---------------------------------------------------------------------------
# Handlers Multimodais (Voz/Audio e Foto/Imagem)
# ---------------------------------------------------------------------------

@bot.message_handler(content_types=["voice", "audio"])
def handle_voice_or_audio(message):
    ctx = _get_authorized_context(message)
    if ctx is None:
        return

    msg_status = bot.reply_to(message, "🎙️ Ouvindo e estruturando sua nota de voz via Gemini...")
    try:
        if message.voice:
            file_id = message.voice.file_id
            mime_type = message.voice.mime_type or "audio/ogg"
        else:
            file_id = message.audio.file_id
            mime_type = message.audio.mime_type or "audio/mpeg"

        file_info = bot.get_file(file_id)
        audio_bytes = bot.download_file(file_info.file_path)

        markdown_content = ctx.processor.process_audio(
            audio_bytes=audio_bytes,
            mime_type=mime_type,
            caption=message.caption or "",
        )
        _, saved_msg = save_note_and_update_moc(markdown_content, ctx=ctx)
        confirmation = format_note_confirmation(saved_msg, markdown_content)
        safe_edit_or_reply(message.chat.id, msg_status.message_id, confirmation)
    except Exception as exc:
        safe_edit_or_reply(message.chat.id, msg_status.message_id, f"❌ Falha ao processar áudio: {exc}")


@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    ctx = _get_authorized_context(message)
    if ctx is None:
        return

    msg_status = bot.reply_to(message, "📸 Analisando imagem (OCR) e estruturando nota via Gemini...")
    try:
        photo = message.photo[-1]
        file_info = bot.get_file(photo.file_id)
        image_bytes = bot.download_file(file_info.file_path)

        markdown_content = ctx.processor.process_image(
            image_bytes=image_bytes,
            mime_type="image/jpeg",
            caption=message.caption or "",
        )
        _, saved_msg = save_note_and_update_moc(markdown_content, ctx=ctx)
        confirmation = format_note_confirmation(saved_msg, markdown_content)
        safe_edit_or_reply(message.chat.id, msg_status.message_id, confirmation)
    except Exception as exc:
        safe_edit_or_reply(message.chat.id, msg_status.message_id, f"❌ Falha ao processar imagem: {exc}")


# ---------------------------------------------------------------------------
# Handler Principal de Texto (Notas, Links e Perguntas com '?')
# ---------------------------------------------------------------------------

@bot.message_handler(func=lambda message: True, content_types=["text"])
def handle_message(message):
    ctx = _get_authorized_context(message)
    if ctx is None:
        return

    text = (message.text or "").strip()
    if not text:
        return

    # 1. Atalho de pergunta ao cofre: começa com '?'
    if text.startswith("?"):
        question = text[1:].strip()
        if question:
            _run_vault_question(message, question, ctx)
            return

    # 2. Deteccao automatica de Link/URL (quando a mensagem contem um link e tem ate 400 chars)
    url_match = re.search(r"(https?://[^\s]+)", text)
    if url_match and len(text) < 400:
        url = url_match.group(1)
        comment = text.replace(url, "").strip()
        msg_status = bot.reply_to(message, "🔗 Lendo link e gerando Nota de Literatura via Gemini...")
        try:
            markdown_content = ctx.processor.process_url(url=url, user_comment=comment)
            _, saved_msg = save_note_and_update_moc(markdown_content, ctx=ctx)
            confirmation = format_note_confirmation(saved_msg, markdown_content)
            safe_edit_or_reply(message.chat.id, msg_status.message_id, confirmation)
        except Exception as exc:
            safe_edit_or_reply(message.chat.id, msg_status.message_id, f"❌ Erro ao processar link: {exc}")
        return

    # 3. Fluxo padrao: Nota de texto livre
    msg_status = bot.reply_to(message, "⏳ Estruturando nota e consultando seu Índice de Tags via Gemini...")
    try:
        markdown_content = ctx.processor.process(text)
        _, saved_msg = save_note_and_update_moc(markdown_content, ctx=ctx)
        confirmation = format_note_confirmation(saved_msg, markdown_content)
        safe_edit_or_reply(message.chat.id, msg_status.message_id, confirmation)
    except Exception as exc:
        safe_edit_or_reply(
            message.chat.id,
            msg_status.message_id,
            f"❌ Erro ao estruturar nota:\n`{exc}`",
        )


if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN nao encontrado no .env.")
    print("🧠 Bot do Segundo Cerebro (Multi-Usuario + Indice MOC + RAG) rodando localmente...")
    try:
        bot.remove_webhook()
    except Exception:
        pass
    bot.infinity_polling()
