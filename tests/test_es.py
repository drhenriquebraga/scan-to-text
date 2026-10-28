import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from scan_to_text import es
from scan_to_text.es import ES, OrigemIntocavel, normalizar_caminho

pytestmark = pytest.mark.skipif(os.name != "nt", reason="E/S segura e regra de Windows")

# Servidor que resolve na própria máquina: o teste não sai para a rede.
UNC = r"\\localhost\scanner-teste"


def _juncao(link: Path, alvo: Path) -> Path:
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(alvo)],
                   check=True, capture_output=True)
    return link


def _mapa(unidade: str) -> str | None:
    return UNC if unidade.upper() == "Z:" else None


@pytest.fixture
def origem(tmp_path) -> Path:
    pasta = tmp_path / "Origem"
    (pasta / "sub").mkdir(parents=True)
    return pasta


# --- normalizar_caminho -------------------------------------------------------

def test_normalizar_tira_prefixo_estendido_e_poe_minusculas(origem):
    esperado = str(origem / "sub" / "x.md").casefold()
    assert normalizar_caminho(Path("\\\\?\\" + str(origem / "sub" / "x.md"))) == esperado
    assert normalizar_caminho(Path(str(origem).upper()) / "SUB" / "X.MD") == esperado


def test_normalizar_unc_estendido_vira_unc_simples():
    assert normalizar_caminho(Path("\\\\?\\UNC\\localhost\\scanner-teste\\a.pdf")) == \
        (UNC + "\\a.pdf").casefold()


def test_normalizar_troca_letra_mapeada_por_unc():
    assert normalizar_caminho(Path(r"Z:\sub\a.pdf"), _mapa) == (UNC + r"\sub\a.pdf").casefold()


def test_normalizar_resolve_juncao(tmp_path, origem):
    link = _juncao(tmp_path / "atalho", origem)
    assert normalizar_caminho(link / "sub" / "novo.md") == \
        str(origem / "sub" / "novo.md").casefold()


def test_normalizar_tira_barra_final_e_fluxo_alternativo(origem):
    assert normalizar_caminho(Path(str(origem) + "\\")) == str(origem).casefold()
    fluxo = Path(str(origem) + "::$INDEX_ALLOCATION") / "x.md"
    assert normalizar_caminho(fluxo) == str(origem / "x.md").casefold()


# --- a origem é intocável, em qualquer forma de caminho ---------------------

@pytest.mark.parametrize("forma", [
    UNC + r"\x.md",
    UNC.upper() + r"\SUB\X.MD",
    "//localhost/scanner-teste/x.md",
    "\\\\?\\UNC\\localhost\\scanner-teste\\x.md",
    "\\\\.\\UNC\\localhost\\scanner-teste\\x.md",
    "\\??\\UNC\\localhost\\scanner-teste\\x.md",
    r"Z:\x.md",
    r"z:\sub\x.md",
    UNC,
])
def test_origem_unc_recusada_em_toda_forma(forma):
    guarda = ES([Path(UNC)], unidade_para_unc=_mapa)
    assert guarda.dentro_de_origem(Path(forma))
    with pytest.raises(OrigemIntocavel):
        guarda.exigir_fora_das_origens(Path(forma))


def test_origem_configurada_por_letra_mapeada_recusa_o_unc():
    guarda = ES([Path("Z:\\")], unidade_para_unc=_mapa)
    assert guarda.dentro_de_origem(Path(UNC + r"\sub\x.md"))


def test_origem_local_recusada_em_toda_forma(origem):
    guarda = ES([origem])
    formas = [
        origem / "x.md",
        origem / "sub" / "novo" / "y.md",
        Path(str(origem).upper()) / "SUB" / "X.MD",
        Path("\\\\?\\" + str(origem / "x.md")),
        Path("\\\\.\\" + str(origem / "x.md")),
        Path("\\??\\" + str(origem / "x.md")),
        Path("//?/" + str(origem / "x.md").replace("\\", "/")),
        Path(str(origem).replace("\\", "/") + "/x.md"),
        Path(str(origem) + ".") / "x.md",
        origem,
    ]
    for forma in formas:
        assert guarda.dentro_de_origem(forma), forma
        with pytest.raises(OrigemIntocavel):
            guarda.exigir_fora_das_origens(forma)


def test_juncao_real_para_a_origem_e_recusada(tmp_path, origem):
    destino = tmp_path / "destino"
    destino.mkdir()
    guarda = ES([origem])
    fora = _juncao(tmp_path / "atalho", origem)
    dentro_do_destino = _juncao(destino / "atalho", origem / "sub")
    assert guarda.dentro_de_origem(fora / "x.md")
    assert guarda.dentro_de_origem(dentro_do_destino / "novo" / "x.md")
    assert not guarda.dentro_de_origem(destino / "PACIENTE" / "x.md")


def test_juncao_e_pega_pela_identidade_mesmo_sem_resolver_o_caminho(tmp_path, origem,
                                                                    monkeypatch):
    # Emula o caminho que o texto não desvenda (compartilhamento da própria
    # estação, por exemplo): só a identidade da pasta no disco o reconhece.
    monkeypatch.setattr(es, "_resolver", lambda caminho: caminho)
    link = _juncao(tmp_path / "atalho", origem)
    assert es.normalizar_caminho(link / "x.md") != es.normalizar_caminho(origem / "x.md")
    assert ES([origem]).dentro_de_origem(link / "sub" / "x.md")


def test_irmao_com_mesmo_prefixo_nao_e_origem(tmp_path, origem):
    guarda = ES([origem])
    vizinho = tmp_path / "Origem2"
    vizinho.mkdir()
    assert not guarda.dentro_de_origem(vizinho / "x.md")
    assert not guarda.dentro_de_origem(tmp_path / "Origem.md")
    assert not guarda.dentro_de_origem(tmp_path)
    guarda.exigir_fora_das_origens(vizinho / "x.md")


def test_recusa_nao_leva_o_caminho_na_mensagem(origem):
    alvo = origem / "JOHN DOE - 2026-09-28" / "x.md"
    with pytest.raises(OrigemIntocavel) as erro:
        ES([origem]).exigir_fora_das_origens(alvo)
    assert "JOHN" not in str(erro.value)
    assert erro.value.caminho == alvo


# --- cópia neutra para a pasta temporária -----------------------------------

def test_copia_neutra_com_sha256_certo_e_origem_intacta(tmp_path, origem):
    conteudo = b"%PDF-1.7 sintetico " * 5000
    arquivo = origem / "FULANA DE TESTE.PDF"
    arquivo.write_bytes(conteudo)
    os.utime(arquivo, (1_700_000_000, 1_700_000_000))
    antes = (hashlib.sha256(arquivo.read_bytes()).hexdigest(), arquivo.stat().st_mtime_ns)

    temp = tmp_path / "temp"
    copia, sha = ES([origem]).copiar_para_temp(arquivo, temp)

    assert sha == hashlib.sha256(conteudo).hexdigest()
    assert copia == temp / f"{sha}.pdf"
    assert copia.read_bytes() == conteudo
    assert sorted(p.name for p in temp.iterdir()) == [f"{sha}.pdf"]
    assert (hashlib.sha256(arquivo.read_bytes()).hexdigest(), arquivo.stat().st_mtime_ns) == antes
    assert sorted(p.name for p in origem.iterdir()) == ["FULANA DE TESTE.PDF", "sub"]


def test_copia_do_mesmo_conteudo_cai_no_mesmo_nome(tmp_path, origem):
    (origem / "a.jpg").write_bytes(b"imagem")
    (origem / "sub" / "b.JPG").write_bytes(b"imagem")
    guarda, temp = ES([origem]), tmp_path / "temp"
    assert guarda.copiar_para_temp(origem / "a.jpg", temp) == \
        guarda.copiar_para_temp(origem / "sub" / "b.JPG", temp)
    assert len(list(temp.iterdir())) == 1


def test_extensao_estranha_nao_entra_no_nome(tmp_path, origem):
    # Depois do último ponto pode vir uma palavra só, e ela pode ser nome.
    guarda, temp = ES([origem]), tmp_path / "temp"
    for nome in ("sem_extensao", "laudo.FULANA DE TESTE", "scan.Fulana", "EXAME JOAO.SILVA",
                 "carta.doc"):
        (origem / nome).write_bytes(nome.encode())
        copia, sha = guarda.copiar_para_temp(origem / nome, temp)
        assert copia.name == sha, nome


def test_extensoes_que_a_leitura_aceita_ficam_no_nome(tmp_path, origem):
    # A leitura escolhe o formato pela extensão da cópia: as duas listas
    # andam juntas.
    from scan_to_text.ler import recipientes
    guarda, temp = ES([origem]), tmp_path / "temp"
    aceitas = set(recipientes.TIPOS) | set(recipientes.HEIF) | {".zip"}
    for i, extensao in enumerate(sorted(aceitas)):
        arquivo = origem / f"a{i}{extensao.upper()}"
        arquivo.write_bytes(extensao.encode())
        copia, sha = guarda.copiar_para_temp(arquivo, temp)
        assert copia.name == sha + extensao


def test_pasta_temporaria_dentro_da_origem_e_recusada(origem):
    (origem / "a.pdf").write_bytes(b"a")
    with pytest.raises(OrigemIntocavel):
        ES([origem]).copiar_para_temp(origem / "a.pdf", origem / "temp")
    assert not (origem / "temp").exists()


class _ResumoQuebrado:
    """Falha no meio da cópia, como disco cheio."""

    @staticmethod
    def sha256():
        class Resumo:
            def update(self, _bloco):
                raise OSError(28, "sem_espaco")
        return Resumo()


def test_falha_no_meio_da_copia_nao_deixa_rascunho(tmp_path, origem, monkeypatch):
    (origem / "a.pdf").write_bytes(b"a" * 10)
    temp = tmp_path / "temp"
    monkeypatch.setattr(es, "hashlib", _ResumoQuebrado)
    with pytest.raises(OSError):
        ES([origem]).copiar_para_temp(origem / "a.pdf", temp)
    assert list(temp.iterdir()) == []
