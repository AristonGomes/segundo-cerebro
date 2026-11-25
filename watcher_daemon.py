"""
watcher_daemon.py
-----------------
Fase 3 do SegundoCerebro - monitoramento em tempo real.

Observa a pasta inbox_raw/ continuamente usando watchdog.
Ao detectar um novo arquivo .txt, aguarda a gravacao completa e o
envia para o NoteProcessor (Fase 1), salvando o resultado no vault
e movendo o original para o backup.

O servico e tolerante a falhas: excecoes de API, parse ou IO sao
logadas e o loop continua. O daemon so encerra via Ctrl+C ou SIGTERM.

Uso:
    python watcher_daemon.py
    python watcher_daemon.py --inbox minha_inbox --vault meu_vault --backup meu_backup
    python watcher_daemon.py --stable-for 2.0 --poll-interval 0.5
"""

import argparse
import logging
import shutil
import signal
import sys
import time
from pathlib import Path

from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from index_manager import IndexManager
from note_processor import APIError, NoteProcessor, NoteProcessorError, ParseError

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
# Argumentos de linha de comando
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SegundoCerebro Watcher — processa .txt novos em tempo real."
    )
    parser.add_argument(
        "--inbox", "-i", default="inbox_raw",
        help="Pasta monitorada (padrao: inbox_raw/)",
    )
    parser.add_argument(
        "--vault", "-v", default="obsidian_vault",
        help="Pasta destino para os .md gerados (padrao: obsidian_vault/)",
    )
    parser.add_argument(
        "--backup", "-b", default="backup_processados",
        help="Pasta de backup para os .txt originais (padrao: backup_processados/)",
    )
    parser.add_argument(
        "--stable-for", type=float, default=1.5, dest="stable_for",
        help="Segundos sem variacao de tamanho para considerar o arquivo pronto (padrao: 1.5)",
    )
    parser.add_argument(
        "--poll-interval", type=float, default=0.3, dest="poll_interval",
        help="Intervalo em segundos entre checagens de tamanho do arquivo (padrao: 0.3)",
    )
    parser.add_argument(
        "--stable-timeout", type=float, default=30.0, dest="stable_timeout",
        help="Tempo maximo de espera pela estabilizacao do arquivo em segundos (padrao: 30.0)",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Helpers de caminho (mesmo padrao do cli_batch_processor)
# ---------------------------------------------------------------------------

def resolve_output_path(vault: Path, source: Path) -> Path:
    return vault / source.with_suffix(".md").name


def resolve_backup_path(backup: Path, source: Path) -> Path:
    dest = backup / source.name
    if not dest.exists():
        return dest
    stem, suffix = source.stem, source.suffix
    counter = 1
    while dest.exists():
        dest = backup / f"{stem}_{counter}{suffix}"
        counter += 1
    return dest


# ---------------------------------------------------------------------------
# Espera pela estabilizacao do arquivo
# ---------------------------------------------------------------------------

def wait_for_stable(
    path: Path,
    stable_for: float = 1.5,
    poll_interval: float = 0.3,
    timeout: float = 30.0,
) -> bool:
    """
    Aguarda ate que o tamanho do arquivo pare de crescer por `stable_for` segundos.

    Necessario porque o evento on_created do watchdog dispara assim que o arquivo
    e criado, antes de o processo escritor concluir a gravacao.

    Returns:
        True se o arquivo estabilizou dentro do timeout.
        False se o timeout foi atingido ou o arquivo desapareceu.
    """
    last_size = -1
    stable_since = None
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            current_size = path.stat().st_size
        except FileNotFoundError:
            log.warning("  Arquivo desapareceu antes de ser lido: %s", path.name)
            return False

        if current_size != last_size:
            last_size = current_size
            stable_since = time.monotonic()
        elif stable_since is not None:
            if (time.monotonic() - stable_since) >= stable_for:
                return True

        time.sleep(poll_interval)

    log.warning("  Timeout ao aguardar estabilizacao de: %s", path.name)
    return False


# ---------------------------------------------------------------------------
# Handler do watchdog
# ---------------------------------------------------------------------------

class NoteHandler(FileSystemEventHandler):
    """
    Reage a eventos de criacao de arquivos .txt na inbox.

    O processamento ocorre na thread de eventos do watchdog, portanto
    qualquer excecao e capturada localmente para nao derrubar o Observer.
    """

    def __init__(
        self,
        processor: NoteProcessor,
        vault: Path,
        backup: Path,
        stable_for: float,
        poll_interval: float,
        stable_timeout: float,
        index_manager: IndexManager | None = None,
    ) -> None:
        super().__init__()
        self._processor     = processor
        self._vault         = vault
        self._backup        = backup
        self._stable_for    = stable_for
        self._poll_interval = poll_interval
        self._stable_timeout = stable_timeout
        self._index_manager = index_manager

    # ---- Evento principal ------------------------------------------------

    def on_created(self, event: FileCreatedEvent) -> None:
        if event.is_directory:
            return

        path = Path(event.src_path)

        if path.suffix.lower() != ".txt":
            return

        log.info("Novo arquivo detectado: %s", path.name)
        self._handle(path)

    # ---- Pipeline de processamento ---------------------------------------

    def _handle(self, path: Path) -> None:
        # 1. Aguarda a gravacao completa
        log.info("  Aguardando estabilizacao do arquivo...")
        if not wait_for_stable(
            path,
            stable_for=self._stable_for,
            poll_interval=self._poll_interval,
            timeout=self._stable_timeout,
        ):
            log.error("  [ERRO] Arquivo ignorado (instavel ou desapareceu): %s", path.name)
            return

        # 2. Leitura
        try:
            raw_text = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            log.error("  [ERRO] Leitura falhou para %s: %s", path.name, exc)
            return

        if not raw_text:
            log.warning("  [AVISO] Arquivo vazio, ignorando: %s", path.name)
            return

        log.info("  Enviando ao Gemini (%d caracteres)...", len(raw_text))

        # 3. Processamento via NoteProcessor
        try:
            result = self._processor.process(raw_text)
        except APIError as exc:
            log.error("  [ERRO DE API] %s", exc)
            return
        except ParseError as exc:
            log.error("  [ERRO DE PARSE] %s", exc)
            return
        except NoteProcessorError as exc:
            log.error("  [ERRO] %s", exc)
            return
        except Exception as exc:
            # Captura qualquer excecao inesperada para nao derrubar o daemon
            log.exception("  [ERRO INESPERADO] %s: %s", type(exc).__name__, exc)
            return

        # 4. Grava o .md no vault
        output_path = resolve_output_path(self._vault, path)
        try:
            output_path.write_text(result, encoding="utf-8")
            log.info("  [OK] Nota salva: %s", output_path)
            if self._index_manager is not None:
                meta = IndexManager.parse_markdown_metadata(output_path.name, result)
                self._index_manager.register_note(
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
            log.error("  [ERRO] Nao foi possivel salvar %s: %s", output_path, exc)
            return

        # 5. Move o .txt original para backup
        backup_path = resolve_backup_path(self._backup, path)
        try:
            shutil.move(str(path), str(backup_path))
            log.info("  [OK] Original movido para: %s", backup_path)
        except OSError as exc:
            # O .md ja foi gerado; apenas avisa, nao desfaz
            log.warning("  [AVISO] Nao foi possivel mover o original: %s", exc)

        log.info("  Pronto. Aguardando proximos arquivos...")


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    inbox  = Path(args.inbox)
    vault  = Path(args.vault)
    backup = Path(args.backup)

    # Valida e cria pastas necessarias
    if not inbox.exists():
        log.error("Pasta inbox nao encontrada: %s", inbox.resolve())
        log.error("Crie a pasta antes de iniciar o watcher.")
        sys.exit(1)

    vault.mkdir(parents=True, exist_ok=True)
    backup.mkdir(parents=True, exist_ok=True)

    # Inicializa o IndexManager e o NoteProcessor (valida API key na largada)
    try:
        index_manager = IndexManager(vault_dir=vault)
        processor = NoteProcessor(index_manager=index_manager)
    except NoteProcessorError as exc:
        log.error("Falha ao inicializar NoteProcessor: %s", exc)
        sys.exit(1)

    # Configura o handler e o observer
    handler = NoteHandler(
        processor=processor,
        vault=vault,
        backup=backup,
        stable_for=args.stable_for,
        poll_interval=args.poll_interval,
        stable_timeout=args.stable_timeout,
        index_manager=index_manager,
    )

    observer = Observer()
    observer.schedule(handler, path=str(inbox), recursive=False)
    observer.start()

    log.info("Watcher iniciado. Monitorando: %s", inbox.resolve())
    log.info("  Vault  : %s", vault.resolve())
    log.info("  Backup : %s", backup.resolve())
    log.info("Pressione Ctrl+C para encerrar.")
    log.info("=" * 55)

    # Tratamento gracioso de SIGTERM (util para uso como servico)
    def _shutdown(signum, frame):
        log.info("Sinal %s recebido. Encerrando watcher...", signum)
        observer.stop()

    signal.signal(signal.SIGTERM, _shutdown)

    try:
        while observer.is_alive():
            observer.join(timeout=1.0)
    except KeyboardInterrupt:
        log.info("Ctrl+C recebido. Encerrando watcher...")
        observer.stop()

    observer.join()
    log.info("Watcher encerrado.")


if __name__ == "__main__":
    main()
