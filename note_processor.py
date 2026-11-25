"""
note_processor.py
-----------------
Motor central do projeto SegundoCerebro (Fases 1 a 9).
Transforma entradas brutas (texto, audio, imagem, links) em notas Obsidian
formatadas em Markdown, com YAML frontmatter, tags padronizadas pelo Indice (MOC),
tarefas extraidas e wikilinks gerados pela API do Gemini.
Tambem oferece consulta interativa (RAG) sobre o cofre e resumo periodico.

SDK: google-genai (pacote atual, substitui o deprecated google-generativeai)
"""

import html
import json
import re
from datetime import date, datetime, timedelta
from typing import Any

import requests
from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL
from index_manager import IndexManager


# ---------------------------------------------------------------------------
# Prompt do sistema enviado ao Gemini
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
Voce e um assistente especializado em organizacao de conhecimento pessoal (PKM) no padrao Obsidian/Zettelkasten.
Sua tarefa e analisar a entrada (texto, transcricao de audio, imagem/OCR ou conteudo de link) e retornar um JSON estruturado com os seguintes campos:

{
  "titulo": "Titulo conciso e descritivo da nota (string)",
  "tags":   ["tag1", "tag2"],
  "resumo": "Paragrafo de resumo executivo da nota (1-3 frases)",
  "corpo":  "Corpo completo bem estruturado em Markdown (usando subtitulos ##, bullet points e wikilinks [[Conceito]] em conceitos-chave ou notas existentes).",
  "tarefas": ["Tarefa ou proximo passo acionavel identificado na nota (lista vazia [] se nao houver)"]
}

Regras:
- Responda APENAS com o JSON valido acima - nenhum texto extra.
- Use wikilinks [[Termo]] somente para conceitos importantes ou para conectar com notas ja existentes no cofre.
- Se uma lista de TAGS EXISTENTES for fornecida no contexto, priorize reutiliza-las sempre que fizer sentido semantico para manter o indice coeso, criando novas tags apenas quando necessario.
- O campo corpo deve preservar e organizar toda a informacao relevante da entrada original.
- Se a entrada for um audio, estruture as ideias faladas de forma limpa e logica (nao apenas uma transcricao crua).
- Se a entrada for uma imagem, extraia o texto relevante (OCR) e sintetize os conceitos visuais.
- Tags devem usar letras minusculas e hifens no lugar de espacos (ex.: filosofia-antiga, machine-learning).
- O JSON deve ser UTF-8 valido.\
"""


# ---------------------------------------------------------------------------
# Excecoes customizadas
# ---------------------------------------------------------------------------

class NoteProcessorError(Exception):
    """Erro base do NoteProcessor."""


class APIError(NoteProcessorError):
    """Falha na comunicacao com a API do Gemini."""


class ParseError(NoteProcessorError):
    """Falha ao interpretar a resposta da API."""


# ---------------------------------------------------------------------------
# Classe principal
# ---------------------------------------------------------------------------

class NoteProcessor:
    """Transforma texto, audio, imagem e links em notas Obsidian via Gemini."""

    def __init__(
        self,
        api_key: str = GEMINI_API_KEY,
        model: str = GEMINI_MODEL,
        index_manager: IndexManager | None = None,
    ):
        if not api_key:
            raise NoteProcessorError(
                "GEMINI_API_KEY nao configurada. "
                "Defina a variavel no arquivo .env (veja .env.example)."
            )
        self._api_key = api_key
        self._client: genai.Client | None = None
        self._model = model
        self._index_manager = index_manager
        self._config = types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            temperature=0.2,
        )

    def _get_client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    # ------------------------------------------------------------------
    # Metodos publicos de captura (Texto, Audio, Imagem, URL)
    # ------------------------------------------------------------------

    def process(self, raw_text: str, fonte: str = "") -> str:
        """
        Recebe texto bruto e retorna uma nota Obsidian completa em Markdown.
        Injeta automaticamente o contexto de tags e notas existentes se o
        IndexManager estiver disponivel.
        """
        raw_text = raw_text.strip()
        if not raw_text:
            raise NoteProcessorError("O texto de entrada esta vazio.")

        prompt_with_context = self._attach_vault_context(raw_text)
        structured = self._call_gemini(prompt_with_context)
        return self._build_markdown(structured, fonte=fonte)

    def process_audio(
        self,
        audio_bytes: bytes,
        mime_type: str = "audio/ogg",
        caption: str = "",
    ) -> str:
        """
        Fase 8: Processa uma nota de voz/audio diretamente via Gemini Multimodal.
        Transcreve, organiza os pensamentos e gera a nota Obsidian.
        """
        if not audio_bytes:
            raise NoteProcessorError("O arquivo de audio esta vazio.")

        instruction = (
            "Ouça atentamente esta nota de voz. Extraia todas as ideias, insights "
            "e eventuais tarefas mencionadas, e estruture uma nota Obsidian completa."
        )
        if caption:
            instruction += f"\nObservacao adicional do autor: {caption}"

        instruction = self._attach_vault_context(instruction)

        audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
        structured = self._call_gemini([audio_part, instruction])
        return self._build_markdown(structured, fonte="nota-de-voz")

    def process_image(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        caption: str = "",
    ) -> str:
        """
        Fase 8: Processa uma foto/captura de tela (pagina de livro, quadro, slide,
        anotacao manuscrita) via OCR visual do Gemini e gera a nota Obsidian.
        """
        if not image_bytes:
            raise NoteProcessorError("A imagem enviada esta vazia.")

        instruction = (
            "Analise esta imagem detalhadamente. Realize a leitura (OCR) de todo "
            "texto visivel (pagina de livro, slide, quadro ou anotacao) e estruture "
            "uma nota Obsidian completa com os conceitos-chave e trechos importantes."
        )
        if caption:
            instruction += f"\nContexto enviado pelo usuario junto a imagem: {caption}"

        instruction = self._attach_vault_context(instruction)

        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        structured = self._call_gemini([image_part, instruction])
        return self._build_markdown(structured, fonte="captura-visual")

    def process_url(self, url: str, user_comment: str = "") -> str:
        """
        Fase 8: Faz a leitura de um link (artigo, pagina web, noticia) e gera
        uma Nota de Literatura estruturada no Obsidian.
        """
        url = url.strip()
        if not url:
            raise NoteProcessorError("URL vazia.")

        extracted_text = self._fetch_url_text(url)
        prompt = (
            f"URL de origem: {url}\n"
            f"Comentario do usuario: {user_comment or 'Nenhum'}\n\n"
            f"Conteudo extraido da pagina:\n{extracted_text}\n\n"
            "Gere uma Nota de Literatura completa sintetizando os argumentos centrais, "
            "pontos-chave e aprendizados deste link."
        )
        prompt_with_context = self._attach_vault_context(prompt)
        structured = self._call_gemini(prompt_with_context)
        return self._build_markdown(structured, fonte=url)

    # ------------------------------------------------------------------
    # Fase 7 & 9: Consulta ao Cofre (RAG) e Resumo Semanal
    # ------------------------------------------------------------------

    def ask_vault(self, question: str, index_manager: IndexManager) -> str:
        """
        Fase 7: Responde a uma pergunta do usuario consultando o Indice Mestre
        e o conteudo completo das notas mais relevantes do cofre.
        """
        question = question.strip()
        if not question:
            raise NoteProcessorError("A pergunta esta vazia.")

        all_notes = index_manager.get_all_notes()
        if not all_notes:
            return (
                "📭 Seu cofre ainda não possui notas indexadas. "
                "Envie algumas anotações primeiro!"
            )

        # 1. Busca as notas mais relevantes para a pergunta
        relevant = index_manager.search(question, limit=6)
        if not relevant:
            relevant = all_notes[:5]

        # 2. Monta dossie com o indice geral + conteudo integral das mais relevantes
        catalog_lines = [
            f"- [[{n['stem']}]] ({n['data']}) | Tags: {', '.join(n['tags'])} | Resumo: {n['resumo']}"
            for n in all_notes[:50]
        ]

        full_contents = []
        for n in relevant:
            body = index_manager.get_note_content(n["filename"])
            if body:
                full_contents.append(f"=== NOTA: [[{n['stem']}]] ({n['titulo']}) ===\n{body[:3500]}")

        rag_prompt = (
            "Voce e o Segundo Cerebro do usuario. Responda a pergunta abaixo "
            "baseando-se nas notas do cofre dele.\n"
            "- Cite sempre as notas usadas no formato `[[Nome_da_Nota]]`.\n"
            "- Se a informacao nao estiver nas notas, avise claramente o que consta no cofre "
            "e complemente de forma prestativa.\n"
            "- Use formatacao Markdown limpa e concisa para leitura no Telegram.\n\n"
            f"CATALOGO GERAL DO COFRE ({len(all_notes)} notas):\n"
            + "\n".join(catalog_lines)
            + "\n\nCONTEUDO DETALHADO DAS NOTAS MAIS RELEVANTES:\n"
            + ("\n\n".join(full_contents) if full_contents else "Apenas resumos disponiveis no indice.")
            + f"\n\nPERGUNTA DO USUARIO:\n{question}"
        )

        try:
            response = self._get_client().models.generate_content(
                model=self._model,
                contents=rag_prompt,
                config=types.GenerateContentConfig(temperature=0.3),
            )
            return (response.text or "").strip()
        except Exception as exc:
            raise APIError(f"Falha ao consultar o cofre via Gemini: {exc}") from exc

    def generate_weekly_digest(self, index_manager: IndexManager, days: int = 7) -> str:
        """
        Fase 9: Gera uma nota de Revisao Periodica / Resumo Semanal conectando
        todas as notas criadas nos ultimos `days` dias e listando tarefas em aberto.
        """
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        all_notes = index_manager.get_all_notes()
        recent_notes = [n for n in all_notes if n.get("data", "") >= cutoff]
        if not recent_notes:
            recent_notes = all_notes[:10]

        if not recent_notes:
            raise NoteProcessorError("Nao ha notas suficientes no cofre para gerar um resumo.")

        notes_dump = []
        for n in recent_notes:
            notes_dump.append(
                f"- Nota: [[{n['stem']}]] | Titulo: {n['titulo']} | Data: {n['data']} | "
                f"Tags: {', '.join(n.get('tags', []))} | Resumo: {n.get('resumo', '')} | "
                f"Tarefas: {n.get('tarefas', [])}"
            )

        digest_prompt = (
            f"Gere uma nota de Revisao Semanal / Sintese de Conhecimento analisando as "
            f"{len(recent_notes)} notas mais recentes do usuario listadas abaixo:\n\n"
            + "\n".join(notes_dump)
            + "\n\nDestaque conexoes entre os temas estudados, principais insights da semana "
            "e consolide os proximos passos/tarefas pendentes."
        )

        structured = self._call_gemini(digest_prompt)
        if "revisao-semanal" not in structured.get("tags", []):
            structured.setdefault("tags", []).append("revisao-semanal")
        return self._build_markdown(structured, fonte="sintese-automatica")

    # ------------------------------------------------------------------
    # Metodos privados auxiliares
    # ------------------------------------------------------------------

    def _attach_vault_context(self, user_input: str) -> str:
        """Anexa as tags e titulos ja existentes no cofre para manter padronizacao."""
        if not self._index_manager:
            return user_input

        ctx = self._index_manager.get_context_for_prompt()
        existing_tags = ctx.get("existing_tags", [])
        existing_notes = ctx.get("existing_notes", [])

        if not existing_tags and not existing_notes:
            return user_input

        context_block = "\n\n---\nCONTEXTO DO COFRE ATUAL (para padronizacao de tags e wikilinks):\n"
        if existing_tags:
            context_block += f"- TAGS JA EXISTENTES (prefira reutiliza-las se pertinente): {', '.join(existing_tags)}\n"
        if existing_notes:
            context_block += f"- NOTAS JA EXISTENTES (use [[Nome_do_Arquivo]] se houver conexao direta): {', '.join(existing_notes)}\n"

        return f"{user_input}{context_block}"

    @staticmethod
    def _fetch_url_text(url: str) -> str:
        """Baixa uma pagina web e extrai o titulo e texto limpo."""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        }
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            raw_html = resp.text
        except Exception as exc:
            return f"[Aviso: Nao foi possivel baixar o HTML completo ({exc}). Analise a partir da URL: {url}]"

        title_match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.IGNORECASE | re.DOTALL)
        page_title = html.unescape(title_match.group(1).strip()) if title_match else ""

        # Remove scripts, styles e tags HTML
        cleaned = re.sub(r"<(script|style|noscript|svg|header|footer|nav)[^>]*>.*?</\1>", " ", raw_html, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r"<[^>]+>", " ", cleaned)
        cleaned = html.unescape(cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()

        return f"Titulo da Pagina: {page_title}\n\nTexto: {cleaned[:15000]}"

    def _call_gemini(self, contents: Any) -> dict:
        """Envia o conteudo (texto ou multimodal) ao Gemini e devolve o dict estruturado."""
        try:
            response = self._get_client().models.generate_content(
                model=self._model,
                contents=contents,
                config=self._config,
            )
        except Exception as exc:
            raise APIError(f"Falha ao chamar a API do Gemini: {exc}") from exc

        raw_json = self._extract_json(response.text or "")
        return self._parse_json(raw_json)

    def _extract_json(self, text: str) -> str:
        """
        Extrai o bloco JSON da resposta, tolerando markdown code fences
        que o modelo as vezes insere.
        """
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fenced:
            return fenced.group(1)

        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start != -1 and brace_end != -1:
            return text[brace_start : brace_end + 1]

        raise ParseError(
            "Nao foi possivel localizar um bloco JSON na resposta da API. "
            f"Resposta recebida:\n{text[:500]}"
        )

    def _parse_json(self, raw_json: str) -> dict:
        """Faz parse do JSON e valida os campos obrigatorios."""
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ParseError(
                f"JSON invalido recebido da API: {exc}\n{raw_json[:300]}"
            ) from exc

        required = {"titulo", "tags", "resumo", "corpo"}
        missing = required - data.keys()
        if missing:
            raise ParseError(f"Campos ausentes no JSON da API: {missing}")

        if "tarefas" not in data or not isinstance(data["tarefas"], list):
            data["tarefas"] = []

        return data

    def _build_markdown(self, data: dict, fonte: str = "") -> str:
        """Monta o documento Markdown final com YAML frontmatter e secao de tarefas."""
        today = date.today().isoformat()

        clean_tags = [
            str(tag).strip().lower().lstrip("#").replace(" ", "-")
            for tag in data.get("tags", [])
            if str(tag).strip()
        ]
        if not clean_tags:
            clean_tags = ["nota"]

        tags_yaml = "\n".join(f'  - "{tag}"' for tag in clean_tags)
        fonte_line = f'fonte: "{fonte}"\n' if fonte else ""

        frontmatter = (
            "---\n"
            f'titulo: "{data["titulo"]}"\n'
            f"data: {today}\n"
            f"{fonte_line}"
            f"tags:\n{tags_yaml}\n"
            "---"
        )

        tarefas = [str(t).strip() for t in data.get("tarefas", []) if str(t).strip()]
        tarefas_section = ""
        if tarefas:
            items = "\n".join(f"- [ ] {t}" for t in tarefas)
            tarefas_section = f"\n\n---\n\n## ✅ Tarefas e Próximos Passos\n\n{items}"

        body = (
            f"# {data['titulo']}\n\n"
            f"> {data['resumo']}\n\n"
            "---\n\n"
            f"{data['corpo']}"
            f"{tarefas_section}"
        )

        return f"{frontmatter}\n\n{body}\n"
