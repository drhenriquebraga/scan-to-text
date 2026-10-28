from pathlib import Path

import checar_literais as cl

RAIZ = Path(__file__).resolve().parents[1]


def _motor(pacote: Path, fonte: str, pasta: str = "entender") -> None:
    (pacote / pasta).mkdir(exist_ok=True)
    (pacote / pasta / "regra.py").write_text(fonte, encoding="utf-8")


def test_rotulo_plantado_reprova(tmp_path):
    _motor(tmp_path, 'ROTULO = "Paciente"\n')
    assert cl.verificar(tmp_path) == ["entender/regra.py:1: 'Paciente'"]


def test_frase_e_acento_reprovam():
    assert cl.texto_de_idioma("nome do paciente")
    assert cl.texto_de_idioma("página")


def test_chave_interna_e_formato_passam():
    for literal in ("cabecalho", "utf-8", "page_header", "{a}/{b}", r"\d{2}/\d{2}/\d{4}", "RGB",
                    "COM[0-9¹²³]"):
        assert not cl.texto_de_idioma(literal), literal


def test_docstring_nao_conta(tmp_path):
    _motor(tmp_path, '"""Lê o PDF e devolve as páginas."""\nX = "corpo"\n')
    assert cl.verificar(tmp_path) == []


def test_fora_do_motor_nao_e_vigiado(tmp_path):
    _motor(tmp_path, 'ROTULO = "Paciente"\n', pasta="tela")
    assert cl.verificar(tmp_path) == []


def test_pacote_real_esta_limpo():
    assert cl.verificar(RAIZ / "app" / "scan_to_text") == []
