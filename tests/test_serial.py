from datetime import date

from scan_to_text.escrever import serial
from scan_to_text.tipos import DataExame, Documento, Linha, Pagina, Tabela


def test_documento_vai_e_volta_igual():
    pagina = Pagina("ab" * 32, 2, blocos=(
        Linha("Paciente: FULANA DE TESTE", (0.1, 0.05, 0.6, 0.08), camada="cabecalho",
              tipo="texto", confianca=0.97, grupo=0),
        Tabela((("Exame", "Resultado"), ("PSA", "0,8")), ((1.0, 1.0), (0.9, 0.7)),
               (0.1, 0.3, 0.9, 0.5), 1)), confianca_media=0.91, rotacao=180)
    doc = Documento(id="abababababab-01", paginas=(pagina,), titulo="PSA TOTAL",
                    instituicao="LABORATORIO EXEMPLO", paciente="FULANA DE TESTE",
                    nascimento=date(1960, 5, 1), categoria=3,
                    data=DataExame(date(2026, 4, 2), "dia", "coleta", "Coleta: 02/04/2026"),
                    cabecalho_original=("LABORATORIO EXEMPLO",), corpo=pagina.blocos,
                    motivos=("regra:laboratorio",))
    assert serial.de_dict(serial.para_dict(doc)) == doc


def test_documento_sem_data_e_sem_nascimento():
    doc = Documento(id="cd" * 6 + "-02", paginas=())
    assert serial.de_dict(serial.para_dict(doc)) == doc
