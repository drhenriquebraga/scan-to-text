import subprocess
from pathlib import Path

import checar_paciente as cp

RAIZ = Path(__file__).resolve().parents[1]


def test_documento_fora_de_fixtures_reprova():
    assert cp.categoria("notas/exame.pdf", b"%PDF-1.7") == "documento"


def test_documento_disfarcado_reprova_pelo_conteudo():
    assert cp.categoria("notas/leia.txt", b"%PDF-1.7 resto") == "documento"
    assert cp.categoria("notas/foto.bin", b"\xff\xd8\xff\xe0") == "documento"


def test_fixture_sintetica_passa():
    assert cp.categoria("tests/fixtures/laudo_falso.pdf", b"%PDF-1.7") is None


def test_saida_do_app_reprova_em_qualquer_lugar():
    assert cp.categoria("MARIA - 2026-09-27/COMPLETO.md", b"# MARIA") == "completo"
    assert cp.categoria("x/_scan-to-text/feitos/ab.json", b"{}") == "saida"
    assert cp.categoria("docs/manifesto.json", b"{}") == "manifesto"
    assert cp.categoria("tests/fixtures/app.log", b"linha") == "log"


def test_codigo_comum_passa():
    assert cp.categoria("app/scan_to_text/__init__.py", b'"""Scan-to-Text."""') is None


def test_laudo_nunca_imprime_o_caminho():
    linhas = cp.laudo([("MARIA DA SILVA - 2026-09-27/COMPLETO.md", "completo")])
    assert linhas and not any("MARIA" in linha for linha in linhas)


def _repo(pasta: Path) -> None:
    for comando in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *comando], cwd=pasta, check=True)


def test_stage_com_pdf_recusa(tmp_path, monkeypatch):
    _repo(tmp_path)
    (tmp_path / "exame.pdf").write_bytes(b"%PDF-1.7 falso")
    subprocess.run(["git", "add", "exame.pdf"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    assert cp.main(["--stage"]) == 1


def test_stage_limpo_passa(tmp_path, monkeypatch):
    _repo(tmp_path)
    (tmp_path / "modulo.py").write_text("X = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "modulo.py"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    assert cp.main(["--stage"]) == 0


def test_gancho_e_sh_lf_ascii_e_chama_a_varredura():
    gancho = (RAIZ / ".githooks" / "pre-commit").read_bytes()
    assert gancho.startswith(b"#!/bin/sh")
    assert b"\r" not in gancho
    assert all(b < 128 for b in gancho)
    assert b"checar_paciente.py" in gancho and b"--stage" in gancho


# imagens do README: só as listadas, pelo conteúdo ---------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"tela de demonstracao"


def _lista(*pares):
    import hashlib
    return "\n".join(f"{hashlib.sha256(c).hexdigest()}  {n}" for n, c in pares).encode()


def test_imagem_listada_pelo_conteudo_passa_na_oficina_e_na_copia():
    lista = _lista(("img/home.png", PNG))
    assert cp.varrer([("publico/img/home.png", PNG), ("publico/img/IMAGENS.sha256", lista)]) == []
    assert cp.varrer([("img/home.png", PNG), ("img/IMAGENS.sha256", lista)]) == []


def test_imagem_trocada_ou_fora_de_img_reprova_mesmo_listada():
    lista = _lista(("img/home.png", PNG), ("docs/scan.png", PNG))
    outro = PNG + b"outra"
    achados = cp.varrer([("publico/img/home.png", outro), ("publico/docs/scan.png", PNG),
                         ("publico/img/IMAGENS.sha256", lista)])
    assert [c for c, _ in achados] == ["publico/img/home.png", "publico/docs/scan.png"]


def test_imagem_sem_lista_reprova():
    assert cp.varrer([("publico/img/home.png", PNG)]) == [("publico/img/home.png", "documento")]


# imagem dentro de texto: o recorte de uma página vai em base64 -----------------

# Montado em partes para este arquivo não casar com a própria regra.
EMBUTIDA = b"data:image/" + b"png;base64,iVBORw0KGgo="
SVG = b'<svg xmlns="http://www.w3.org/2000/svg">\n<image href="' + EMBUTIDA + b'"/>\n</svg>\n'


def test_svg_fora_da_lista_reprova():
    assert cp.varrer([("publico/img/scan.svg", SVG)]) == [("publico/img/scan.svg", "documento")]
    assert cp.categoria("docs/diagrama.svg", b"<svg/>") == "documento"


def test_svg_listado_passa_tambem_com_o_crlf_do_checkout():
    lista = _lista(("img/fluxo.svg", SVG))
    assert cp.varrer([("publico/img/fluxo.svg", SVG), ("publico/img/IMAGENS.sha256", lista)]) == []
    crlf = SVG.replace(b"\n", b"\r\n")
    assert cp.varrer([("img/fluxo.svg", crlf), ("img/IMAGENS.sha256", lista)]) == []


def test_imagem_embutida_em_texto_reprova():
    assert cp.categoria("docs/leia.md", b"![pagina](" + EMBUTIDA + b")") == "documento"
    assert cp.categoria("app/tela.html", b'<img src="' + EMBUTIDA.upper() + b'">') == "documento"
    assert cp.categoria("tests/fixtures/pagina.md", b"![](" + EMBUTIDA + b")") is None


def test_toda_imagem_do_readme_esta_na_lista():
    pasta = RAIZ / "publico" / "img"
    itens = [(f"publico/img/{p.name}", p.read_bytes()) for p in sorted(pasta.iterdir())]
    assert cp.varrer(itens) == []
