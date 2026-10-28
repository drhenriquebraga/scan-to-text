"""Ponta a ponta com OCR de verdade: PDF escaneado sintético -> árvore no destino."""
from datetime import date

import pytest

from scan_to_text import modelos
from scan_to_text import pacote as pacotes
from scan_to_text.es import ES
from scan_to_text.processar import Contexto, processar
from sinteticos import laudo_laboratorio

try:
    PASTA = modelos.pasta_modelos()
except modelos.ModelosAusentes:
    PASTA = None

pytestmark = [pytest.mark.modelos, pytest.mark.skipif(PASTA is None, reason="modelos ausentes")]


def test_laudo_escaneado_chega_na_pasta_do_paciente(tmp_path):
    from scan_to_text.ler.layout import Leitor

    origem, destino = tmp_path / "scanner", tmp_path / "destino"
    origem.mkdir()
    destino.mkdir()
    arquivo = origem / "scan001.pdf"
    laudo_laboratorio().save(arquivo, resolution=300)
    ctx = Contexto(pacote=pacotes.carregar("pt-BR"), leitor=Leitor(PASTA), es=ES([origem]),
                   destinos=[destino], estacao="estacao-teste", pid=4242,
                   hoje=date(2026, 10, 25), pasta_temp=tmp_path / "temp")
    resumo = processar(arquivo, ctx)
    assert resumo["situacao"] == "feito"
    pasta = destino / "FULANA DE TESTE SINTETICA - 2026-10-25"
    assert pasta.is_dir(), sorted(p.name for p in destino.iterdir())
    [md] = list((pasta / "3 LABORATORIO").glob("*.md"))
    assert md.name == "2026-04-02 - HEMOGRAMA COMPLETO.md"
    texto = md.read_text(encoding="utf-8")
    assert "Hemoglobina" in texto and "12,4" in texto
    assert "DADOS NUMÉRICOS ISOLADOS NÃO SÃO CONFIÁVEIS" in texto
    assert "Beltrano" not in pasta.name
