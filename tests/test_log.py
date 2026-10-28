import logging
import os
from datetime import date
from pathlib import Path

import pytest

from scan_to_text import log

HASH = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"


class ErroSintetico(Exception):
    """Tipo de exceção carregado, para o filtro reconhecer o nome dele."""


@pytest.fixture
def pasta_logs(tmp_path):
    raiz = logging.getLogger()
    nivel_raiz = raiz.level
    nosso = logging.getLogger(log.NOME)
    nivel_nosso = nosso.level
    pasta = tmp_path / "logs"
    yield pasta
    for handler in list(raiz.handlers):
        if str(getattr(handler, "baseFilename", "")).startswith(str(pasta)):
            raiz.removeHandler(handler)
            handler.close()
    raiz.setLevel(nivel_raiz)
    nosso.setLevel(nivel_nosso)


def _texto(pasta: Path) -> str:
    for handler in logging.getLogger().handlers:
        handler.flush()
    return "".join(p.read_text(encoding="utf-8") for p in sorted(pasta.iterdir()))


def test_configurar_grava_evento_com_codigo_e_campos(pasta_logs):
    logger = log.configurar(pasta_logs)
    assert logger.name == log.NOME
    log.evento("arquivo_lido", paginas=3, em_branco=1, ok=True, sha256=HASH, motor="rapidocr")
    texto = _texto(pasta_logs)
    assert "arquivo_lido paginas=3 em_branco=1 ok=True" in texto
    assert f"sha256={HASH} motor=rapidocr" in texto


def test_rotacao_de_8_mb_e_um_handler_so(pasta_logs):
    log.configurar(pasta_logs)
    log.configurar(pasta_logs)
    nossos = [h for h in logging.getLogger().handlers
              if str(getattr(h, "baseFilename", "")).startswith(str(pasta_logs))]
    assert len(nossos) == 1
    assert nossos[0].maxBytes == 8 * 1024 * 1024
    assert nossos[0].backupCount >= 1


def test_argumento_que_nao_e_codigo_vira_redigido(pasta_logs):
    logger = log.configurar(pasta_logs)
    log.evento("documento_gravado", paciente="FULANA DE TESTE", pasta=Path("C:/x/FULANA"),
               titulo="Hemograma", data=date(2026, 4, 2), pagina=2, confianca=0.93)
    logger.info("caminho %s tamanho %d", r"C:\dest\FULANA DE TESTE - 2026-09-28", 10)
    logger.info("mapa %(nome)s %(n)d", {"nome": "FULANA", "n": 4})
    logger.info(Path("FULANA DE TESTE.pdf"))
    texto = _texto(pasta_logs)
    assert "FULANA" not in texto and "Hemograma" not in texto and "2026-04-02" not in texto
    assert "documento_gravado paciente=<redigido> pasta=<redigido>" in texto
    assert "pagina=2 confianca=0.93" in texto
    assert "caminho <redigido> tamanho 10" in texto
    assert "mapa <redigido> 4" in texto


def test_so_digitos_e_pontuacao_nao_passam_como_codigo(pasta_logs):
    log.configurar(pasta_logs)
    log.evento("versoes", docling="v2.130.0", data="02.04.2026", iso="2026-04-02",
               cartao="123456789012345", paginas=[1, 2], pedaco=HASH[:12])
    texto = _texto(pasta_logs)
    assert (f"versoes docling=v2.130.0 data=<redigido> iso=<redigido> cartao=<redigido> "
            f"paginas=[1, 2] pedaco={HASH[:12]}") in texto


def test_codigo_e_uma_palavra_so_com_sublinhado(pasta_logs):
    # Ponto e hífen deixavam passar nome de arquivo inteiro e nome composto.
    log.configurar(pasta_logs)
    log.evento("arquivo", arquivo="john.doe.pdf", nome="maria-silva", motivo="pdf_senha",
               docling="v2.130.0", porta="localhost:8080", versao="2.130.0")
    texto = _texto(pasta_logs)
    assert ("arquivo arquivo=<redigido> nome=<redigido> motivo=pdf_senha docling=v2.130.0 "
            "porta=<redigido> versao=<redigido>") in texto


def test_mensagem_montada_antes_da_chamada_e_redigida(pasta_logs):
    logger = log.configurar(pasta_logs)
    nome = "FULANA DE TESTE"
    logger.info(f"lendo {nome} pagina_lida")
    logger.info("montada %s" % nome)
    logger.warning(f"ciclo_erro tipo=PermissionError tipo_no_filho=ErroSintetico\n"
                   f"ciclo.py:193 _limpar_destinos\nprocessar.py:130 processar {nome}")
    texto = _texto(pasta_logs)
    assert "FULANA" not in texto and "TESTE" not in texto
    assert "lendo <redigido> <redigido> <redigido> pagina_lida" in texto
    assert "montada <redigido> <redigido> <redigido>" in texto
    # Nome de tipo de exceção e quadro do rastro são código, não gente.
    assert "ciclo_erro tipo=PermissionError tipo_no_filho=ErroSintetico\n" in texto
    assert "ciclo.py:193 _limpar_destinos\nprocessar.py:130 processar <redigido>" in texto


def test_terceiro_com_mensagem_ja_montada_fica_so_com_o_lugar(pasta_logs):
    # A biblioteca formata antes de chamar (f-string com o texto da exceção),
    # e texto de documento em minúsculas passaria palavra por palavra.
    log.configurar(pasta_logs)
    terceiro = logging.getLogger("docling.backend")
    terceiro.warning("erro na celula hemoglobina 12,4 de fulana")
    terceiro.warning("Pagina %d sem texto", 3)
    terceiro.warning("aviso_de_terceiro %s", "pagina_vazia")
    texto = _texto(pasta_logs)
    assert "hemoglobina" not in texto and "Pagina" not in texto
    assert "WARNING docling.backend" in texto
    assert texto.count("<redigido> test_log.py:") == 2
    assert "aviso_de_terceiro pagina_vazia" in texto


def test_terceiros_so_de_warning_para_cima(pasta_logs):
    log.configurar(pasta_logs, nivel=logging.DEBUG)
    terceiro = logging.getLogger("biblioteca_de_terceiro")
    terceiro.setLevel(logging.DEBUG)
    terceiro.info("detalhe_de_terceiro")
    terceiro.warning("aviso_de_terceiro %s", "FULANA DE TESTE")
    logging.getLogger(log.NOME + ".vigia").debug("detalhe_nosso")
    texto = _texto(pasta_logs)
    assert "detalhe_de_terceiro" not in texto
    assert "aviso_de_terceiro <redigido>" in texto
    assert "detalhe_nosso" in texto


def test_rastro_de_erro_fica_sem_caminho_e_sem_mensagem_livre(pasta_logs):
    logger = log.configurar(pasta_logs)
    pasta = pasta_logs.parent / "FULANA DE TESTE - 2026-09-28"
    try:
        (pasta / "laudo.md").read_bytes()
    except OSError:
        logger.exception("falha_leitura")
    try:
        try:
            raise ValueError("titulo " + pasta.name)
        except ValueError as erro:
            raise RuntimeError("codigo_interno") from erro
    except RuntimeError:
        logger.exception("falha_encadeada")
    texto = _texto(pasta_logs)
    assert "FULANA" not in texto
    assert "falha_leitura" in texto and "FileNotFoundError" in texto and "errno=2" in texto
    assert "RuntimeError: codigo_interno" in texto and "ValueError: <redigido>" in texto
    assert "test_log.py" in texto


def test_evento_sem_configurar_nao_quebra():
    log.evento("antes_de_configurar", n=1)


def test_log_de_processamento_sintetico_nao_tem_o_nome(pasta_logs, tmp_path, monkeypatch):
    """Critério 16: processar "JOHN DOE" e não achar o nome em log nenhum."""
    from scan_to_text import registro
    from scan_to_text.es import ES, OrigemIntocavel
    from scan_to_text.escrever import atomico

    logger = log.configurar(pasta_logs, nivel=logging.DEBUG)
    nome = "JOHN DOE"
    origem, destino, temp = tmp_path / "origem", tmp_path / "destino", tmp_path / "temp"
    origem.mkdir()
    arquivo = origem / f"{nome}.pdf"
    arquivo.write_bytes(b"%PDF-1.7 " + nome.encode())
    guarda = ES([origem])

    copia, sha = guarda.copiar_para_temp(arquivo, temp)
    pasta = destino / f"{nome} - 2026-09-28"
    atomico.gravar(pasta / "3 LABORATORIO" / "2026-04-02 - HEMOGRAMA.md",
                   f"# HEMOGRAMA\n\nPaciente: {nome}  \n".encode(), guarda, "RECEPCAO", 4242)
    atomico.copiar(copia, pasta / "ORIGINAIS" / arquivo.name, guarda, "RECEPCAO", 4242)

    chamadas = []
    substituir = os.replace

    def antivirus(a, b):
        chamadas.append(1)
        if len(chamadas) == 1:
            raise PermissionError(32, "em_uso", str(b))
        substituir(a, b)

    monkeypatch.setattr(atomico, "_esperar", lambda _s: None)
    monkeypatch.setattr(atomico.os, "replace", antivirus)
    registro.gravar_manifesto(pasta, {
        "pasta": pasta.name, "primeiro_processamento": "2026-09-28T10:00:00",
        "ultima_atualizacao": "2026-09-28T10:00:00",
        "paciente": {"exibido": nome, "variantes": [nome], "nascimento": None, "confianca": 0.9},
        "documentos": [], "originais": [f"ORIGINAIS/{arquivo.name}"], "versoes": {},
    }, guarda, "RECEPCAO", 4242)
    monkeypatch.undo()
    registro.marcar_feito(destino, sha, [pasta.name], guarda, "RECEPCAO", 4242)
    assert registro.achar_pasta(destino, nome, None, lambda a, b: a == b, str.upper) == pasta

    try:
        atomico.gravar(origem / f"{nome}.md", b"x", guarda, "RECEPCAO", 4242)
    except OrigemIntocavel:
        logger.exception("escrita_recusada")
    log.evento("documento_gravado", paciente=nome, pasta=str(pasta), sha256=sha, paginas=1)
    logging.getLogger("docling").warning("lendo %s", arquivo)
    # Mensagens montadas antes da chamada, nossas e de terceiros.
    logger.info(f"lendo {nome}")
    logger.info("pre-formatado %s" % nome)
    logging.getLogger("docling.pipeline").warning(f"Falha na pagina: Paciente {nome}")
    logging.getLogger("docling.pipeline").warning(f"falha com {nome.lower()}")
    log.evento("arquivo", nome="john-doe", arquivo="john.doe.pdf")

    texto = _texto(pasta_logs)
    assert texto.strip()
    assert "JOHN" not in texto and "DOE" not in texto
    assert "john" not in texto.casefold()
