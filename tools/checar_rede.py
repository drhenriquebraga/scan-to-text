"""Portão de rede: reprova módulo do app que chame função de rede.

A lista é de PERMITIDOS: módulo novo que fale para fora cai aqui por omissão.
A detecção lê o nome CHAMADO no AST, e não a palavra no texto, para não acusar
a explicação de um docstring. `connect` fica de fora porque também abre
arquivo (`sqlite3.connect`); um `sock.connect()` exige antes um
`socket.socket(...)`, que já casa por `socket`.

Portado de `tools/check_project.py` (checar_consulta_de_versao_nao_leva_nada)
do local-suite, Apache-2.0.
"""
import ast
import sys
from pathlib import Path

PERMITIDOS: dict[str, str] = {
    "scan_to_text/servidor.py": "escuta só em 127.0.0.1, porta efêmera, para a tela local",
}
REDE = ("urlopen", "urlretrieve", "HTTPSConnection", "HTTPConnection", "socket",
        "gethostbyname", "getaddrinfo", "create_connection", "sendall")
REDE_MODULOS = ("urllib", "requests", "httpx", "ftplib", "smtplib", "telnetlib",
                "xmlrpc")


def verificar(raiz_app: Path) -> list[str]:
    intrusos = []
    for arquivo in sorted(raiz_app.rglob("*.py")):
        relativo = arquivo.relative_to(raiz_app).as_posix()
        if relativo in PERMITIDOS:
            continue
        arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if not isinstance(no, ast.Call):
                continue
            funcao = no.func
            alvo = getattr(funcao, "attr", None) or getattr(funcao, "id", None)
            recebedor = getattr(getattr(funcao, "value", None), "id", None)
            if alvo in REDE:
                intrusos.append(f"{relativo}: {alvo}()")
            elif recebedor in REDE_MODULOS:
                intrusos.append(f"{relativo}: {recebedor}.{alvo}()")
    return sorted(set(intrusos))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    intrusos = verificar(Path(__file__).resolve().parents[1] / "app")
    for i in intrusos:
        print(i)
    print("rede: ok" if not intrusos else f"rede: {len(intrusos)} chamada(s) fora da lista")
    return 1 if intrusos else 0


if __name__ == "__main__":
    raise SystemExit(main())
