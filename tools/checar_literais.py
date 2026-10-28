"""Portão de literais: o código do motor não tem texto de idioma.

Rótulos, meses, títulos e padrões moram em `app/packs/<idioma>/*.yaml`. Aqui
se reprova, nas pastas do motor, literal de string (fora de docstring) que
pareça texto: letra fora do ASCII, palavra com inicial maiúscula ou duas
palavras separadas por espaço. Chave interna (`cabecalho`), formato (`utf-8`)
e sigla (`RGB`) passam.
"""
import ast
import re
import sys
import unicodedata
from pathlib import Path

PASTAS_DO_MOTOR = ("entender", "ler", "escrever")
_CAPITALIZADA = re.compile(r"\b[A-Z][a-z]{2,}")
_DUAS_PALAVRAS = re.compile(r"[A-Za-z]{2,}\s+[A-Za-z]{2,}")


def _letra_fora_do_ascii(literal: str) -> bool:
    # Só letra conta: dígito sobrescrito (nome reservado do Windows) e símbolo
    # não são texto de idioma.
    return any(ord(c) > 127 and unicodedata.category(c).startswith("L") for c in literal)


def texto_de_idioma(literal: str) -> bool:
    return bool(_letra_fora_do_ascii(literal) or _CAPITALIZADA.search(literal)
                or _DUAS_PALAVRAS.search(literal))


def _docstrings(arvore: ast.AST) -> set[int]:
    ids = set()
    for no in ast.walk(arvore):
        if isinstance(no, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            primeiro = no.body[0] if no.body else None
            if (isinstance(primeiro, ast.Expr) and isinstance(primeiro.value, ast.Constant)
                    and isinstance(primeiro.value.value, str)):
                ids.add(id(primeiro.value))
    return ids


def verificar(raiz_pacote: Path) -> list[str]:
    achados = []
    for pasta in PASTAS_DO_MOTOR:
        base = raiz_pacote / pasta
        if not base.is_dir():
            continue
        for arquivo in sorted(base.rglob("*.py")):
            arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
            docs = _docstrings(arvore)
            relativo = arquivo.relative_to(raiz_pacote).as_posix()
            for no in ast.walk(arvore):
                if (isinstance(no, ast.Constant) and isinstance(no.value, str)
                        and id(no) not in docs and texto_de_idioma(no.value)):
                    achados.append(f"{relativo}:{no.lineno}: {no.value[:40]!r}")
    return achados


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    achados = verificar(Path(__file__).resolve().parents[1] / "app" / "scan_to_text")
    for a in achados:
        print(a)
    print("literais: ok" if not achados else f"literais: {len(achados)} texto(s) no motor")
    return 1 if achados else 0


if __name__ == "__main__":
    raise SystemExit(main())
