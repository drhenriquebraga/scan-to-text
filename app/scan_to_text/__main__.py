"""Entrada: python -m scan_to_text [preflight | autoteste [--sem-janela]].

Sem subcomando, abre o app: é o que o atalho e o INSTALL.cmd chamam, pelo
pythonw, que não tem console nem stdout.
"""
import json
import sys


def main(argv: list[str] | None = None) -> int:
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:] if argv is None else argv
    if not args:
        from scan_to_text.principal import executar
        return executar()
    if args[:1] == ["preflight"]:
        from scan_to_text.preflight import preflight
        print(json.dumps(preflight(), ensure_ascii=False, indent=2))
        return 0
    if args[:1] == ["autoteste"]:
        from scan_to_text.autoteste import autoteste
        resultado = autoteste(janela="--sem-janela" not in args)
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
        return 0 if resultado["ok"] else 1
    print("uso: python -m scan_to_text preflight | autoteste [--sem-janela]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
