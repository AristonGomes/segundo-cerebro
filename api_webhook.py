"""
api_webhook.py
--------------
Fases 4 a 9 do SegundoCerebro - Camada de API HTTP Completa + Webhook Serverless (Vercel).

Suporta:
- Webhook nativo do Telegram (`POST /telegram/webhook` e `GET /telegram/setup_webhook`)
  para rodar 24/7 gratuitamente na Vercel sem precisar de servidor ligado.
- Captura de texto com padronizacao de tags via Indice (MOC)
- Captura multimodal (audio/notas de voz, fotos/OCR e links/URLs)
- Consulta interativa ao cofre (RAG / pergunte ao seu Segundo Cerebro)
- Geracao de Resumo Semanal e gestao do Mapa de Conteudo (00_Indice_MOC.md)
"""

import asyncio
import base64
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from index_manager import IndexManager
from note_processor import APIError, NoteProcessor, NoteProcessorError, ParseError
from telegram_bot import bind_services, bot, process_telegram_update

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App e inicializacao (Lazy + Startup) compativel com Vercel Serverless
# ---------------------------------------------------------------------------

app = FastAPI(
    title="SegundoCerebro API & Telegram Webhook",
    description=(
        "Assistente completo de PKM (Obsidian + Gemini): captura multimodal "
        "(texto, audio, imagem, links), Indice Mestre de Tags (MOC), "
        "consulta inteligente (RAG), revisao periodica e Webhook Serverless (Vercel)."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

_index_manager: IndexManager | None = None
_processor: NoteProcessor | None = None


def _ensure_initialized() -> tuple[NoteProcessor, IndexManager]:
    """
    Inicializa sob demanda (lazy) o IndexManager e o NoteProcessor.
    Essencial para ambientes Serverless como a Vercel.
    """
    global _index_manager, _processor
    if _index_manager is None or _processor is None:
        try:
            _index_manager = IndexManager()
            _processor = NoteProcessor(index_manager=_index_manager)
            bind_services(_processor, _index_manager)
            log.info(
                "SegundoCerebro inicializado | %d notas | %d tags",
                len(_index_manager.get_all_notes()),
                len(_index_manager.get_all_tags()),
            )
        except NoteProcessorError as exc:
            log.error("Falha ao inicializar NoteProcessor: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"NoteProcessor nao inicializado: {exc}",
            )
    return _processor, _index_manager


@app.on_event("startup")
async def startup() -> None:
    try:
        _ensure_initialized()
    except HTTPException:
        pass


# ---------------------------------------------------------------------------
# Schemas Pydantic
# ---------------------------------------------------------------------------

class SenderMetadata(BaseModel):
    platform: str | None = Field(default=None, examples=["telegram", "whatsapp", "web"])
    sender_id: str | None = Field(default=None, examples=["123456789"])
    sender_name: str | None = Field(default=None, examples=["Ariston"])
    message_id: str | None = None
    chat_id: str | None = None


class NoteRequest(BaseModel):
    raw_text: str = Field(..., min_length=1, max_length=50_000)
    metadata: SenderMetadata | None = None


class MediaRequest(BaseModel):
    media_type: str = Field(..., pattern="^(audio|image)$", description="'audio' ou 'image'")
    data_base64: str = Field(..., min_length=1, description="Conteudo binario codificado em base64")
    mime_type: str = Field(default="audio/ogg", description="MIME type (ex: audio/ogg, image/jpeg)")
    caption: str = Field(default="", description="Legenda ou observacao enviada junto com a midia")
    metadata: SenderMetadata | None = None


class UrlRequest(BaseModel):
    url: str = Field(..., min_length=4, description="URL do artigo ou pagina web")
    comment: str = Field(default="", description="Comentario opcional do usuario sobre o link")
    metadata: SenderMetadata | None = None


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, description="Pergunta a ser respondida com base no cofre")


class DigestRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=90, description="Janela de dias para o resumo")


class RegisterNoteRequest(BaseModel):
    filename: str
    markdown: str


class NoteResponse(BaseModel):
    status: str = "ok"
    markdown: str
    char_count: int
    processed_at: str
    sender_id: str | None = None
    platform: str | None = None


class ErrorDetail(BaseModel):
    status: str = "error"
    error_type: str
    message: str


# ---------------------------------------------------------------------------
# Handlers de erro globais
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Excecao nao tratada em %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorDetail(
            error_type="InternalServerError",
            message="Erro interno inesperado. Tente novamente.",
        ).model_dump(),
    )


# ---------------------------------------------------------------------------
# Endpoints de Info & Health
# ---------------------------------------------------------------------------

@app.get("/", tags=["Info"])
async def root() -> dict[str, Any]:
    return {
        "service": "SegundoCerebro API (Vercel Serverless Ready)",
        "version": app.version,
        "docs": "/docs",
        "endpoints": {
            "health": "GET /health",
            "telegram_webhook": "POST /telegram/webhook",
            "telegram_setup_webhook": "GET /telegram/setup_webhook",
            "process_note": "POST /process_note",
            "process_media": "POST /process_media",
            "process_url": "POST /process_url",
            "ask": "POST /ask",
            "weekly_digest": "POST /weekly_digest",
            "index": "GET /index",
            "register_note": "POST /index/register",
        },
    }


@app.get("/health", tags=["Info"])
async def health() -> dict[str, Any]:
    _, idx = _ensure_initialized()
    return {
        "status": "healthy",
        "processor": "ready",
        "total_notes": len(idx.get_all_notes()),
        "total_tags": len(idx.get_all_tags()),
    }


# ---------------------------------------------------------------------------
# Endpoints do Webhook do Telegram (Vercel 24/7 Gratuito)
# ---------------------------------------------------------------------------

@app.post("/telegram/webhook", tags=["Telegram Serverless"])
async def telegram_webhook(request: Request) -> dict[str, bool]:
    """
    Recebe atualizacoes (mensagens de texto, audio, foto, comandos) enviadas
    diretamente pelo servidor do Telegram via Webhook HTTP.
    """
    _ensure_initialized()
    update_dict = await request.json()
    await asyncio.to_thread(process_telegram_update, update_dict)
    return {"ok": True}


@app.get("/telegram/setup_webhook", tags=["Telegram Serverless"])
async def setup_telegram_webhook(
    request: Request,
    url: str = Query(default="", description="URL base opcional (detectada automaticamente se omitida)"),
) -> dict[str, Any]:
    """
    Ativa o Webhook do Telegram apontando automaticamente para este dominio na Vercel!
    Basta abrir `https://seu-app.vercel.app/telegram/setup_webhook` no navegador.
    """
    _ensure_initialized()
    base_url = url.strip().rstrip("/")
    if not base_url:
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
        proto = request.headers.get("x-forwarded-proto", "https")
        base_url = f"{proto}://{host}"

    webhook_url = f"{base_url}/telegram/webhook"
    try:
        bot.remove_webhook()
        ok = bot.set_webhook(url=webhook_url)
        return {
            "status": "ok" if ok else "failed",
            "webhook_url": webhook_url,
            "message": "Webhook do Telegram configurado com sucesso! Seu bot ja esta online 24/7.",
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Falha ao registrar webhook no Telegram: {exc}",
        )


@app.get("/telegram/remove_webhook", tags=["Telegram Serverless"])
async def remove_telegram_webhook() -> dict[str, Any]:
    """Remove o Webhook do Telegram (util caso queira voltar a rodar via polling local)."""
    try:
        ok = bot.remove_webhook()
        return {"status": "ok" if ok else "failed", "message": "Webhook removido."}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Falha ao remover webhook: {exc}",
        )


# ---------------------------------------------------------------------------
# Endpoints REST de Captura e Consulta
# ---------------------------------------------------------------------------

@app.post("/process_note", response_model=NoteResponse, tags=["Capture"])
async def process_note(payload: NoteRequest) -> NoteResponse:
    processor, _ = _ensure_initialized()
    meta = payload.metadata

    try:
        markdown = await asyncio.to_thread(processor.process, payload.raw_text)
    except NoteProcessorError as exc:
        code = (
            status.HTTP_502_BAD_GATEWAY
            if isinstance(exc, (APIError, ParseError))
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(
            status_code=code,
            detail=ErrorDetail(error_type=type(exc).__name__, message=str(exc)).model_dump(),
        )

    return NoteResponse(
        markdown=markdown,
        char_count=len(markdown),
        processed_at=datetime.now(timezone.utc).isoformat(),
        sender_id=meta.sender_id if meta else None,
        platform=meta.platform if meta else None,
    )


@app.post("/process_media", response_model=NoteResponse, tags=["Capture"])
async def process_media(payload: MediaRequest) -> NoteResponse:
    processor, _ = _ensure_initialized()
    meta = payload.metadata

    try:
        raw_bytes = base64.b64decode(payload.data_base64)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorDetail(error_type="InvalidBase64", message=str(exc)).model_dump(),
        )

    try:
        if payload.media_type == "audio":
            markdown = await asyncio.to_thread(
                processor.process_audio, raw_bytes, payload.mime_type, payload.caption
            )
        else:
            markdown = await asyncio.to_thread(
                processor.process_image, raw_bytes, payload.mime_type, payload.caption
            )
    except NoteProcessorError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(error_type=type(exc).__name__, message=str(exc)).model_dump(),
        )

    return NoteResponse(
        markdown=markdown,
        char_count=len(markdown),
        processed_at=datetime.now(timezone.utc).isoformat(),
        sender_id=meta.sender_id if meta else None,
        platform=meta.platform if meta else None,
    )


@app.post("/process_url", response_model=NoteResponse, tags=["Capture"])
async def process_url(payload: UrlRequest) -> NoteResponse:
    processor, _ = _ensure_initialized()
    meta = payload.metadata

    try:
        markdown = await asyncio.to_thread(
            processor.process_url, payload.url, payload.comment
        )
    except NoteProcessorError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(error_type=type(exc).__name__, message=str(exc)).model_dump(),
        )

    return NoteResponse(
        markdown=markdown,
        char_count=len(markdown),
        processed_at=datetime.now(timezone.utc).isoformat(),
        sender_id=meta.sender_id if meta else None,
        platform=meta.platform if meta else None,
    )


@app.post("/ask", tags=["Knowledge & Index"])
async def ask_vault_endpoint(payload: AskRequest) -> dict[str, Any]:
    processor, idx = _ensure_initialized()
    try:
        answer = await asyncio.to_thread(processor.ask_vault, payload.question, idx)
    except NoteProcessorError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(error_type=type(exc).__name__, message=str(exc)).model_dump(),
        )
    return {
        "status": "ok",
        "question": payload.question,
        "answer": answer,
        "matching_notes": [n["stem"] for n in idx.search(payload.question, limit=5)],
    }


@app.post("/weekly_digest", response_model=NoteResponse, tags=["Knowledge & Index"])
async def weekly_digest(payload: DigestRequest) -> NoteResponse:
    processor, idx = _ensure_initialized()
    try:
        markdown = await asyncio.to_thread(
            processor.generate_weekly_digest, idx, payload.days
        )
    except NoteProcessorError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorDetail(error_type=type(exc).__name__, message=str(exc)).model_dump(),
        )

    return NoteResponse(
        markdown=markdown,
        char_count=len(markdown),
        processed_at=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/index", tags=["Knowledge & Index"])
async def get_index(q: str = Query(default="", description="Filtro opcional de busca")) -> dict[str, Any]:
    _, idx = _ensure_initialized()
    notes = idx.search(q, limit=20) if q else idx.get_all_notes()
    return {
        "status": "ok",
        "total_notes": len(idx.get_all_notes()),
        "tags": idx.get_all_tags(),
        "pending_tasks": idx.get_pending_tasks(),
        "notes": notes,
        "moc_filename": "00_Indice_MOC.md",
        "moc_markdown": idx.generate_moc_markdown(),
    }


@app.post("/index/register", tags=["Knowledge & Index"])
async def register_note_in_index(payload: RegisterNoteRequest) -> dict[str, Any]:
    _, idx = _ensure_initialized()
    meta = IndexManager.parse_markdown_metadata(payload.filename, payload.markdown)
    entry = idx.register_note(
        filename=payload.filename,
        titulo=meta["titulo"],
        tags=meta["tags"],
        resumo=meta["resumo"],
        data_criacao=meta["data"],
        tarefas=meta["tarefas"],
        fonte=meta["fonte"],
    )
    return {
        "status": "ok",
        "entry": entry,
        "moc_filename": "00_Indice_MOC.md",
        "moc_markdown": idx.generate_moc_markdown(),
    }
