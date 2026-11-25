"""
index_manager.py
----------------
Fase 6 do SegundoCerebro - Indice Mestre (MOC - Map of Content) e Catalogo de Tags.
Suporta tanto execucao local (`obsidian_vault/`) quanto execucao Serverless na
Vercel (`/tmp/obsidian_vault` + persistencia em nuvem no Google Drive).
"""

import json
import logging
import os
import re
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from drive_uploader import fetch_file_from_drive, upload_note_to_drive

log = logging.getLogger(__name__)

MOC_FILENAME = "00_Indice_MOC.md"
INDEX_JSON_FILENAME = "_index.json"


def resolve_writable_vault_dir(preferred: str | Path = "obsidian_vault") -> Path:
    """
    Retorna a pasta do vault. Na Vercel (onde o sistema de arquivos raiz e
    somente-leitura), usa automaticamente `/tmp/<preferred>` mantendo o
    isolamento por usuario.
    """
    rel_path = Path(preferred)
    if rel_path.is_absolute():
        rel_path = Path("obsidian_vault") / rel_path.name

    if os.getenv("VERCEL"):
        vault = Path(tempfile.gettempdir()) / rel_path
        vault.mkdir(parents=True, exist_ok=True)
        return vault

    vault = Path(preferred)
    try:
        vault.mkdir(parents=True, exist_ok=True)
        return vault
    except OSError:
        fallback = Path(tempfile.gettempdir()) / rel_path
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


class IndexManager:
    """Gerencia o catalogo de notas, taxonomia de tags e geracao do MOC."""

    def __init__(
        self,
        vault_dir: str | Path = "obsidian_vault",
        app_script_url: str | None = None,
    ) -> None:
        self.vault_dir = resolve_writable_vault_dir(vault_dir)
        self.index_path = self.vault_dir / INDEX_JSON_FILENAME
        self.moc_path = self.vault_dir / MOC_FILENAME
        self.app_script_url = (
            app_script_url if app_script_url is not None else os.getenv("APPS_SCRIPT_URL", "")
        )
        self._data: dict[str, Any] = self._load_or_initialize()

    # ------------------------------------------------------------------
    # Inicializacao e Persistencia (Local + Google Drive)
    # ------------------------------------------------------------------

    def _load_or_initialize(self) -> dict[str, Any]:
        """
        Carrega o `_index.json` local, ou tenta baixa-lo do Google Drive
        (em caso de cold start na Vercel), ou inicializa varrendo o vault.
        """
        if self.index_path.exists():
            try:
                raw = self.index_path.read_text(encoding="utf-8")
                data = json.loads(raw)
                if isinstance(data, dict) and "notes" in data:
                    return data
            except Exception as exc:
                log.warning("Falha ao ler %s local: %s", self.index_path, exc)

        # Em ambiente Serverless (ou primeira execucao), tenta restaurar do Drive
        if self.app_script_url:
            remote_json = fetch_file_from_drive(INDEX_JSON_FILENAME, self.app_script_url)
            if remote_json:
                try:
                    data = json.loads(remote_json)
                    if isinstance(data, dict) and "notes" in data:
                        self.index_path.write_text(remote_json, encoding="utf-8")
                        log.info("Indice restaurado do Google Drive (%d notas).", len(data["notes"]))
                        return data
                except Exception as exc:
                    log.warning("Indice remoto invalido, inicializando novo: %s", exc)

        data = {"updated_at": "", "notes": {}}
        self._data = data
        self.rebuild_from_vault(sync_drive=False)
        return self._data

    def save(self, sync_drive: bool = True) -> None:
        """Persiste o `_index.json`, regenera o `00_Indice_MOC.md` e sincroniza com o Drive."""
        self._data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        json_str = json.dumps(self._data, ensure_ascii=False, indent=2)
        self.index_path.write_text(json_str, encoding="utf-8")
        self.generate_moc_file()

        if sync_drive and self.app_script_url:
            try:
                upload_note_to_drive(
                    INDEX_JSON_FILENAME,
                    json_str,
                    self.app_script_url,
                    overwrite=True,
                )
            except Exception as exc:
                log.warning("Aviso ao salvar _index.json no Drive: %s", exc)

    # ------------------------------------------------------------------
    # Registro e Varredura de Notas
    # ------------------------------------------------------------------

    def register_note(
        self,
        filename: str,
        titulo: str,
        tags: list[str],
        resumo: str,
        data_criacao: str | None = None,
        tarefas: list[str] | None = None,
        fonte: str | None = None,
        sync_drive: bool = True,
    ) -> dict[str, Any]:
        """Adiciona ou atualiza uma nota no catalogo e regenera o MOC."""
        if filename == MOC_FILENAME:
            return {}

        stem = Path(filename).stem
        clean_tags = sorted(
            {t.strip().lower().lstrip("#") for t in (tags or []) if t.strip()}
        )
        date_str = data_criacao or datetime.now().strftime("%Y-%m-%d")

        entry = {
            "filename": filename,
            "stem": stem,
            "titulo": titulo.strip() or stem,
            "data": date_str,
            "tags": clean_tags,
            "resumo": resumo.strip(),
            "tarefas": [t.strip() for t in (tarefas or []) if t.strip()],
            "fonte": fonte or "",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        self._data["notes"][filename] = entry
        self.save(sync_drive=sync_drive)
        return entry

    def rebuild_from_vault(self, sync_drive: bool = False) -> int:
        """Varre todos os arquivos .md do vault local e sincroniza no indice."""
        count = 0
        for md_file in sorted(self.vault_dir.glob("*.md")):
            if md_file.name == MOC_FILENAME:
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
                parsed = self.parse_markdown_metadata(md_file.name, content)
                self._data["notes"][md_file.name] = parsed
                count += 1
            except Exception as exc:
                log.warning("Nao foi possivel indexar %s: %s", md_file.name, exc)

        self.save(sync_drive=sync_drive)
        return count

    @staticmethod
    def parse_markdown_metadata(filename: str, content: str) -> dict[str, Any]:
        """Extrai titulo, data, tags, resumo e tarefas de um arquivo Markdown."""
        stem = Path(filename).stem
        titulo = stem.replace("_", " ")
        data_str = datetime.now().strftime("%Y-%m-%d")
        tags: list[str] = []
        fonte = ""

        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if fm_match:
            fm_text = fm_match.group(1)
            t_match = re.search(r'titulo:\s*"?([^"\n]+)"?', fm_text)
            if t_match:
                titulo = t_match.group(1).strip()

            d_match = re.search(r"data:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", fm_text)
            if d_match:
                data_str = d_match.group(1).strip()

            f_match = re.search(r'fonte:\s*"?([^"\n]+)"?', fm_text)
            if f_match:
                fonte = f_match.group(1).strip()

            tags = [
                t.strip().lower().lstrip("#")
                for t in re.findall(r'^\s*-\s*"?([^"\n]+)"?', fm_text, re.MULTILINE)
                if t.strip()
            ]

        resumo = ""
        resumo_match = re.search(r"^>\s*(.+)$", content, re.MULTILINE)
        if resumo_match:
            resumo = resumo_match.group(1).strip()

        tarefas = [
            t.strip()
            for t in re.findall(r"^\s*-\s*\[\s\]\s*(.+)$", content, re.MULTILINE)
            if t.strip()
        ]

        return {
            "filename": filename,
            "stem": stem,
            "titulo": titulo,
            "data": data_str,
            "tags": sorted(set(tags)),
            "resumo": resumo,
            "tarefas": tarefas,
            "fonte": fonte,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    # ------------------------------------------------------------------
    # Consultas de Contexto e Busca
    # ------------------------------------------------------------------

    def get_all_tags(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for note in self._data.get("notes", {}).values():
            for tag in note.get("tags", []):
                counts[tag] += 1
        return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))

    def get_all_notes(self) -> list[dict[str, Any]]:
        notes = list(self._data.get("notes", {}).values())
        return sorted(
            notes,
            key=lambda n: (n.get("data", ""), n.get("updated_at", "")),
            reverse=True,
        )

    def get_context_for_prompt(self, max_tags: int = 60, max_notes: int = 40) -> dict[str, list[str]]:
        tags = list(self.get_all_tags().keys())[:max_tags]
        notes = [
            f"{n['stem']} ({n['titulo']})"
            for n in self.get_all_notes()[:max_notes]
        ]
        return {"existing_tags": tags, "existing_notes": notes}

    def get_pending_tasks(self) -> list[dict[str, Any]]:
        result = []
        for note in self.get_all_notes():
            tarefas = note.get("tarefas", [])
            if tarefas:
                result.append(
                    {
                        "titulo": note["titulo"],
                        "stem": note["stem"],
                        "data": note["data"],
                        "tarefas": tarefas,
                    }
                )
        return result

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        q = query.strip().lower().lstrip("#")
        if not q:
            return self.get_all_notes()[:limit]

        terms = [t for t in re.split(r"\s+", q) if len(t) >= 2]
        if not terms:
            terms = [q]

        scored: list[tuple[int, dict[str, Any]]] = []
        for note in self.get_all_notes():
            score = 0
            tags = [t.lower() for t in note.get("tags", [])]
            titulo = note.get("titulo", "").lower()
            resumo = note.get("resumo", "").lower()
            stem = note.get("stem", "").lower()

            for term in terms:
                if term in tags:
                    score += 10
                elif any(term in t for t in tags):
                    score += 6
                if term in titulo or term in stem:
                    score += 8
                if term in resumo:
                    score += 4

            md_path = self.vault_dir / note["filename"]
            if md_path.exists():
                try:
                    body = md_path.read_text(encoding="utf-8").lower()
                    for term in terms:
                        if term in body:
                            score += 2
                except OSError:
                    pass

            if score > 0:
                scored.append((score, note))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored[:limit]]

    def get_note_content(self, filename: str) -> str:
        """Le o conteudo completo da nota localmente ou do Google Drive se necessario."""
        path = self.vault_dir / filename
        if path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                pass
        if self.app_script_url:
            remote = fetch_file_from_drive(filename, self.app_script_url)
            if remote:
                return remote
        return ""

    # ------------------------------------------------------------------
    # Geracao do Mapa de Conteudo (00_Indice_MOC.md)
    # ------------------------------------------------------------------

    def generate_moc_markdown(self) -> str:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        notes = self.get_all_notes()
        tag_counts = self.get_all_tags()
        pending_tasks = self.get_pending_tasks()

        lines: list[str] = [
            "---",
            'titulo: "00 - Mapa de Conteúdo (MOC) & Índice de Tags"',
            f"data: {datetime.now().strftime('%Y-%m-%d')}",
            f'atualizado_em: "{now_str}"',
            "tags:",
            '  - "moc"',
            '  - "indice"',
            '  - "segundo-cerebro"',
            "---",
            "",
            "# 🧠 Mapa de Conteúdo (MOC) — Segundo Cérebro",
            "",
            f"> **Painel Central de Conhecimento** | **{len(notes)}** notas catalogadas | "
            f"**{len(tag_counts)}** tags únicas | Atualizado em `{now_str}`",
            "",
            "---",
            "",
        ]

        if pending_tasks:
            lines.append("## ✅ Tarefas e Próximos Passos Pendentes")
            lines.append("")
            for item in pending_tasks:
                lines.append(f"### [[{item['stem']}|{item['titulo']}]] *({item['data']})*")
                for task in item["tarefas"]:
                    lines.append(f"- [ ] {task}")
                lines.append("")
            lines.append("---")
            lines.append("")

        lines.append("## 🏷️ Índice por Tags (Taxonomia)")
        lines.append("")

        if not tag_counts:
            lines.append("_Nenhuma tag registrada ainda. Envie sua primeira nota pelo Telegram!_")
            lines.append("")
        else:
            tag_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for note in notes:
                for tag in note.get("tags", []):
                    tag_map[tag].append(note)

            pills = " · ".join(f"`#{t}` ({c})" for t, c in tag_counts.items())
            lines.append(f"**Visão Rápida:** {pills}")
            lines.append("")

            for tag, count in tag_counts.items():
                plural = "nota" if count == 1 else "notas"
                lines.append(f"### #{tag} ({count} {plural})")
                for n in tag_map[tag]:
                    resumo_curto = n.get("resumo", "")
                    if len(resumo_curto) > 140:
                        resumo_curto = resumo_curto[:137] + "..."
                    lines.append(
                        f"- [[{n['stem']}|{n['titulo']}]] *({n['data']})* — {resumo_curto}"
                    )
                lines.append("")

        lines.append("---")
        lines.append("")

        lines.append("## 📚 Histórico Cronológico de Notas")
        lines.append("")
        if not notes:
            lines.append("_Nenhuma nota encontrada._")
        else:
            lines.append("| Data | Nota | Tags | Resumo |")
            lines.append("| :--- | :--- | :--- | :--- |")
            for n in notes:
                tags_fmt = " ".join(f"`#{t}`" for t in n.get("tags", []))
                resumo_tabela = n.get("resumo", "").replace("|", "/").replace("\n", " ")
                if len(resumo_tabela) > 100:
                    resumo_tabela = resumo_tabela[:97] + "..."
                lines.append(
                    f"| {n['data']} | [[{n['stem']}\\|{n['titulo']}]] | {tags_fmt} | {resumo_tabela} |"
                )
        lines.append("")
        lines.append("---")
        lines.append("")

        lines.extend(
            [
                "## ⚡ Consulta Dinâmica (Obsidian Dataview)",
                "",
                "> [!TIP] Dica para Obsidian",
                "> Se você instalar o plugin comunitário **Dataview** no seu Obsidian, o bloco abaixo monta uma tabela dinâmica automática de todas as notas:",
                "",
                "```dataview",
                'TABLE titulo AS "Título", data AS "Data", tags AS "Tags"',
                'FROM ""',
                f'WHERE file.name != "{Path(MOC_FILENAME).stem}"',
                "SORT data DESC",
                "```",
                "",
            ]
        )

        return "\n".join(lines)

    def generate_moc_file(self) -> Path:
        moc_content = self.generate_moc_markdown()
        self.moc_path.write_text(moc_content, encoding="utf-8")
        return self.moc_path
