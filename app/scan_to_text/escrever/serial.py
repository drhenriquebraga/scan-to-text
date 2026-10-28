"""Documento <-> JSON, para guardar cada documento na pasta do paciente.

Com o documento guardado, o COMPLETO.md é regenerado a cada arquivo novo e um
exame repetido que chega em outro dia é reconhecido. O JSON fica dentro da
pasta do paciente (`_scan-to-text/documentos/`), que é área de dado de
paciente: nunca vai para log.
"""
from datetime import date

from scan_to_text.tipos import DataExame, Documento, Linha, Pagina, Tabela


def _bloco_para_dict(bloco) -> dict:
    if isinstance(bloco, Linha):
        return {"tipo": "linha", "texto": bloco.texto, "caixa": list(bloco.caixa),
                "camada": bloco.camada, "forma": bloco.tipo, "confianca": bloco.confianca,
                "grupo": bloco.grupo}
    return {"tipo": "tabela", "celulas": [list(l) for l in bloco.celulas],
            "confiancas": [list(l) for l in bloco.confiancas], "caixa": list(bloco.caixa),
            "linhas_de_cabecalho": bloco.linhas_de_cabecalho}


def _bloco_de_dict(d: dict):
    if d["tipo"] == "linha":
        return Linha(texto=d["texto"], caixa=tuple(d["caixa"]), camada=d["camada"],
                     tipo=d["forma"], confianca=d["confianca"], grupo=d["grupo"])
    return Tabela(celulas=tuple(tuple(l) for l in d["celulas"]),
                  confiancas=tuple(tuple(l) for l in d["confiancas"]),
                  caixa=tuple(d["caixa"]), linhas_de_cabecalho=d["linhas_de_cabecalho"])


def _data(valor: str | None) -> date | None:
    return date.fromisoformat(valor) if valor else None


def para_dict(doc: Documento) -> dict:
    return {
        "id": doc.id,
        "paginas": [{"arquivo_sha256": p.arquivo_sha256, "indice": p.indice,
                     "blocos": [_bloco_para_dict(b) for b in p.blocos],
                     "em_branco": p.em_branco, "confianca_media": p.confianca_media,
                     "rotacao": p.rotacao} for p in doc.paginas],
        "titulo": doc.titulo, "instituicao": doc.instituicao, "paciente": doc.paciente,
        "nascimento": doc.nascimento.isoformat() if doc.nascimento else None,
        "categoria": doc.categoria,
        "data": {"valor": doc.data.valor.isoformat() if doc.data.valor else None,
                 "precisao": doc.data.precisao, "papel": doc.data.papel,
                 "trecho": doc.data.trecho},
        "cabecalho_original": list(doc.cabecalho_original),
        "corpo": [_bloco_para_dict(b) for b in doc.corpo],
        "repetido_de": doc.repetido_de, "motivos": list(doc.motivos),
    }


def de_dict(d: dict) -> Documento:
    return Documento(
        id=d["id"],
        paginas=tuple(Pagina(arquivo_sha256=p["arquivo_sha256"], indice=p["indice"],
                             blocos=tuple(_bloco_de_dict(b) for b in p["blocos"]),
                             em_branco=p["em_branco"], confianca_media=p["confianca_media"],
                             rotacao=p["rotacao"]) for p in d["paginas"]),
        titulo=d["titulo"], instituicao=d["instituicao"], paciente=d["paciente"],
        nascimento=_data(d["nascimento"]), categoria=d["categoria"],
        data=DataExame(valor=_data(d["data"]["valor"]), precisao=d["data"]["precisao"],
                       papel=d["data"]["papel"], trecho=d["data"]["trecho"]),
        cabecalho_original=tuple(d["cabecalho_original"]),
        corpo=tuple(_bloco_de_dict(b) for b in d["corpo"]),
        repetido_de=d["repetido_de"], motivos=tuple(d["motivos"]),
    )
