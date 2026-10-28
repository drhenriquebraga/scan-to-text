from pathlib import Path

import checar_rede as cr

RAIZ = Path(__file__).resolve().parents[1]


def _arquivo(pasta: Path, nome: str, fonte: str) -> None:
    (pasta / nome).write_text(fonte, encoding="utf-8")


def test_urlopen_plantado_reprova(tmp_path):
    _arquivo(tmp_path, "ruim.py", "import urllib.request\nurllib.request.urlopen('http://x')\n")
    assert cr.verificar(tmp_path) == ["ruim.py: urlopen()"]


def test_modulo_de_saida_reprova(tmp_path):
    _arquivo(tmp_path, "pedido.py", "import requests\nrequests.get('http://x')\n")
    assert cr.verificar(tmp_path) == ["pedido.py: requests.get()"]


def test_subpasta_tambem_e_vigiada(tmp_path):
    (tmp_path / "ler").mkdir()
    _arquivo(tmp_path / "ler", "baixa.py", "import socket\nsocket.create_connection(('x', 1))\n")
    assert cr.verificar(tmp_path) == ["ler/baixa.py: create_connection()"]


def test_ouvir_e_nao_falar_passa(tmp_path):
    _arquivo(tmp_path, "bom.py", (
        "import socket, sqlite3\n"
        "socket.gethostname()\n"
        "sqlite3.connect('config.db')\n"))
    assert cr.verificar(tmp_path) == []


def test_explicacao_em_docstring_nao_conta(tmp_path):
    _arquivo(tmp_path, "doc.py", '"""Aqui nao ha urlopen nem requests.get."""\n')
    assert cr.verificar(tmp_path) == []


def test_app_real_nao_fala_para_fora():
    assert cr.verificar(RAIZ / "app") == []
