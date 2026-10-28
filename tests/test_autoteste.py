import pytest

from scan_to_text import autoteste


def test_veredito_segue_o_criterio_da_spec():
    assert autoteste.veredito(15.0, 2048) == "docling_completo"
    assert autoteste.veredito(31.0, 2048) == "reprovado"
    assert autoteste.veredito(15.0, 900) == "reprovado"


def test_etapa_registra_tempo_e_erro_legivel():
    ok = autoteste.etapa("soma", lambda: 1 + 1)
    assert ok["ok"] and ok["detalhe"] == "" and ok["segundos"] >= 0
    falha = autoteste.etapa("dll", lambda: (_ for _ in ()).throw(OSError("DLL load failed")))
    assert not falha["ok"] and falha["detalhe"] == "OSError: DLL load failed"


def test_paginas_de_teste_tem_tabela_e_pagina_invertida():
    paginas = autoteste.paginas_de_teste()
    assert [nome for nome, _ in paginas] == ["laudo", "tabela", "invertida"]
    assert all(img.size == (2480, 3508) for _, img in paginas)


def test_leitura_com_leitor_falso_mede_e_confere_o_texto():
    from scan_to_text.tipos import Linha, Pagina, Tabela

    class LeitorFalso:
        def ler_imagem(self, imagem, sha, indice):
            return Pagina(sha, indice, blocos=(
                Linha("Hemoglobina 12,4", (0, 0, 1, 0.1)),
                Tabela((("Exame",), ("Hemoglobina",)), ((1.0,), (1.0,)), (0, 0.2, 1, 0.4))),
                rotacao=180 if indice == 2 else 0)

    medida = autoteste.medir_leitura(LeitorFalso(), autoteste.paginas_de_teste())
    assert medida["paginas"] == 3 and medida["texto_conferido"] and medida["orientacao_certa"]


@pytest.mark.modelos
def test_autoteste_de_verdade_nesta_maquina():
    from scan_to_text import modelos
    try:
        modelos.pasta_modelos()
    except modelos.ModelosAusentes:
        pytest.skip("modelos ausentes")
    r = autoteste.autoteste(janela=False)
    assert r["ok"], r["etapas"]
    assert r["segundos_por_pagina"] > 0 and r["pico_mb"] > 0
    assert r["veredito"] in ("docling_completo", "tabelas_rapidas", "so_rapidocr")
