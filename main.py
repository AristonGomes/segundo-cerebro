"""
main.py
-------
Ponto de entrada do SegundoCerebro.
Le 'teste_input.txt', processa via NoteProcessor e grava 'teste_output.md'.

Uso:
    python main.py
    python main.py --input minha_nota.txt --output resultado.md
"""

import argparse
import sys
from pathlib import Path

from note_processor import APIError, NoteProcessor, NoteProcessorError, ParseError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SegundoCerebro — converte nota bruta em nota Obsidian."
    )
    parser.add_argument(
        "--input", "-i",
        default="teste_input.txt",
        help="Arquivo de texto bruto de entrada (padrao: teste_input.txt)",
    )
    parser.add_argument(
        "--output", "-o",
        default="teste_output.md",
        help="Arquivo Markdown de saida (padrao: teste_output.md)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    # --- Leitura do arquivo de entrada ---
    if not input_path.exists():
        print(f"[ERRO] Arquivo de entrada nao encontrado: {input_path}", file=sys.stderr)
        sys.exit(1)

    raw_text = input_path.read_text(encoding="utf-8")
    print(f"[OK] Lido: {input_path} ({len(raw_text)} caracteres)")

    # --- Processamento ---
    try:
        processor = NoteProcessor()
        result = processor.process(raw_text)
    except NoteProcessorError as exc:
        print(f"[ERRO de configuracao] {exc}", file=sys.stderr)
        sys.exit(1)
    except APIError as exc:
        print(f"[ERRO de API] {exc}", file=sys.stderr)
        sys.exit(1)
    except ParseError as exc:
        print(f"[ERRO de parse] {exc}", file=sys.stderr)
        sys.exit(1)

    # --- Gravacao do resultado ---
    output_path.write_text(result, encoding="utf-8")
    print(f"[OK] Nota gerada: {output_path}")
    print("-" * 40)
    print(result)


if __name__ == "__main__":
    main()
