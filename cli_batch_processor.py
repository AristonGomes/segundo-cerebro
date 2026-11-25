"""
cli_batch_processor.py
----------------------
Fase 2 do SegundoCerebro — processamento em lote.

Varre a pasta de entrada (inbox_raw/) em busca de arquivos .txt,
processa cada um via NoteProcessor e organiza os resultados:

  inbox_raw/          →  arquivos .txt de entrada
  obsidian_vault/     →  notas .md geradas
  backup_processados/ →  .txt originais movidos apos processamento

Uso:
    python cli_batch_processor.py
    python cli_batch_processor.py --inbox minha_inbox --vault meu_vault --backup meu_backup
    python cli_batch_processor.py --dry-run   # simula sem chamar a API
"""

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

from index_manager import IndexManager
from note_processor import APIError, NoteProcessor, NoteProcessorError, ParseError

# ---------------------------------------------------------------------------
# Configuracao de logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Argumentos de linha de comando
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SegundoCerebro — processa lote de notas .txt para Obsidian."
    )
    parser.add_argument(
        "--inbox", "-i",
        default="inbox_raw",
        help="Pasta com os .txt de entrada (padrao: inbox_raw/)",
    )
    parser.add_argument(
        "--vault", "-v",
        default="obsidian_vault",
        help="Pasta destino para os .md gerados (padrao: obsidian_vault/)",
    )
    parser.add_argument(
        "--backup", "-b",
        default="backup_processados",
        help="Pasta para mover os .txt originais apos processar (padrao: backup_processados/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula o processamento sem chamar a API nem mover arquivos.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Pausa em segundos entre chamadas a API para respeitar rate limits (padrao: 1.0).",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Helpers de pasta
# ---------------------------------------------------------------------------

def ensure_dirs(*paths: Path) -> None:
    """Cria as pastas necessarias se nao existirem."""
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)


def resolve_output_path(vault: Path, source: Path) -> Path:
    """
    Deriva o caminho .md de destino a partir do nome do .txt de origem.
    Ex: inbox_raw/reuniao_q3.txt  →  obsidian_vault/reuniao_q3.md
    """
    return vault / source.with_suffix(".md").name


def resolve_backup_path(backup: Path, source: Path) -> Path:
    """
    Deriva o caminho de backup do .txt original.
    Adiciona sufixo numerico se ja existir um arquivo com o mesmo nome.
    """
    dest = backup / source.name
    if not dest.exists():
        return dest

    stem = source.stem
    suffix = source.suffix
    counter = 1
    while dest.exists():
        dest = backup / f"{stem}_{counter}{suffix}"
        counter += 1
    return dest


# ---------------------------------------------------------------------------
# Processamento de um unico arquivo
# ---------------------------------------------------------------------------

def process_file(
    txt_path: Path,
    vault: Path,
    backup: Path,
    processor: NoteProcessor,
    dry_run: bool,
    index_manager: IndexManager | None = None,
) -> bool:
    """
    Processa um unico arquivo .txt.

    Returns:
        True se bem-sucedido, False se houve erro (o arquivo NAO e movido).
    """
    log.info("Processando: %s", txt_path.name)

    # --- Leitura ---
    try:
        raw_text = txt_path.read_text(encoding="utf-8")
    except OSError as exc:
        log.error("  [ERRO] Nao foi possivel ler o arquivo: %s", exc)
        return False

    if not raw_text.strip():
        log.warning("  [AVISO] Arquivo vazio, ignorando: %s", txt_path.name)
        return False

    if dry_run:
        log.info("  [DRY-RUN] Simulacao — nenhuma chamada a API realizada.")
        log.info("  [DRY-RUN] Destino seria: %s", resolve_output_path(vault, txt_path))
        return True

    # --- Chamada ao NoteProcessor ---
    try:
        result = processor.process(raw_text)
    except APIError as exc:
        log.error("  [ERRO DE API] %s", exc)
        return False
    except ParseError as exc:
        log.error("  [ERRO DE PARSE] %s", exc)
        return False
    except NoteProcessorError as exc:
        log.error("  [ERRO] %s", exc)
        return False

    # --- Gravacao do .md ---
    output_path = resolve_output_path(vault, txt_path)
    try:
        output_path.write_text(result, encoding="utf-8")
        log.info("  [OK] Nota salva: %s", output_path)
        if index_manager is not None:
            meta = IndexManager.parse_markdown_metadata(output_path.name, result)
            index_manager.register_note(
                filename=output_path.name,
                titulo=meta["titulo"],
                tags=meta["tags"],
                resumo=meta["resumo"],
                data_criacao=meta["data"],
                tarefas=meta["tarefas"],
                fonte=meta["fonte"],
            )
            log.info("  [OK] Indice MOC atualizado.")
    except OSError as exc:
        log.error("  [ERRO] Nao foi possivel gravar %s: %s", output_path, exc)
        return False

    # --- Mover o .txt original para backup ---
    backup_path = resolve_backup_path(backup, txt_path)
    try:
        shutil.move(str(txt_path), str(backup_path))
        log.info("  [OK] Original movido para backup: %s", backup_path)
    except OSError as exc:
        log.warning(
            "  [AVISO] Nota gerada, mas nao foi possivel mover o original: %s", exc
        )
        # Nao retorna False — o arquivo .md ja foi gerado com sucesso.

    return True


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    inbox  = Path(args.inbox)
    vault  = Path(args.vault)
    backup = Path(args.backup)

    # --- Valida pasta de entrada ---
    if not inbox.exists():
        log.error("Pasta de entrada nao encontrada: %s", inbox.resolve())
        log.error("Crie a pasta e adicione arquivos .txt antes de rodar.")
        sys.exit(1)

    # --- Cria pastas de saida ---
    ensure_dirs(vault, backup)

    # --- Coleta arquivos ---
    txt_files = sorted(inbox.glob("*.txt"))
    if not txt_files:
        log.info("Nenhum arquivo .txt encontrado em: %s", inbox.resolve())
        sys.exit(0)

    log.info("Encontrados %d arquivo(s) .txt em '%s'", len(txt_files), inbox)
    if args.dry_run:
        log.info("Modo DRY-RUN ativado — nenhuma alteracao sera feita.")

    # --- Instancia o processador (valida API key antes de iniciar o lote) ---
    if not args.dry_run:
        try:
            index_manager = IndexManager(vault_dir=vault)
            processor = NoteProcessor(index_manager=index_manager)
        except NoteProcessorError as exc:
            log.error("Falha ao inicializar NoteProcessor: %s", exc)
            sys.exit(1)
    else:
        index_manager = None
        processor = None  # nao sera usado em dry-run

    # --- Laco principal ---
    total = len(txt_files)
    success = 0
    failed  = 0

    for index, txt_path in enumerate(txt_files, start=1):
        log.info("─" * 50)
        log.info("[%d/%d] %s", index, total, txt_path.name)

        ok = process_file(txt_path, vault, backup, processor, args.dry_run, index_manager)

        if ok:
            success += 1
        else:
            failed += 1

        # Pausa entre chamadas para respeitar rate limits
        if not args.dry_run and index < total and args.delay > 0:
            log.info("  Aguardando %.1fs antes do proximo arquivo...", args.delay)
            time.sleep(args.delay)

    # --- Sumario final ---
    log.info("═" * 50)
    log.info("Processamento concluido.")
    log.info("  Total    : %d", total)
    log.info("  Sucesso  : %d", success)
    log.info("  Falhas   : %d", failed)
    log.info("  Vault    : %s", vault.resolve())
    log.info("  Backup   : %s", backup.resolve())

    if failed > 0:
        sys.exit(1)  # sinal de erro para scripts/CI


if __name__ == "__main__":
    main()
