"""Um arquivo de origem de ponta a ponta.

Cópia neutra -> trava em cada destino que ainda não tem o arquivo -> leitura
(uma vez só) -> entendimento -> escrita em cada destino, sob a trava de
escrita dele -> feitos. A trava do arquivo vem antes do OCR, para duas
estações nunca lerem o mesmo arquivo. O resumo devolvido não tem nome de
paciente nem texto de documento: vai para o log.
"""
from __future__ import annotations

import hashlib
import shutil
import tempfile
import time
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

from scan_to_text import registro
from scan_to_text.entender import (atribuir as atribuicao, classificar, datas, identidade,
                                   limpar, repetidos, segmentar)
from scan_to_text.es import ES
from scan_to_text.escrever import arvore
from scan_to_text.ler import recipientes
from scan_to_text.ler.pdf import Ilegivel
from scan_to_text.log import evento
from scan_to_text.tipos import Pagina, Resultado
from scan_to_text.travas import Batimento, Trava, arquivo_de_observacoes

OBSERVADAS = "observadas.json"


@dataclass
class Contexto:
    pacote: object
    leitor: object                 # tem ler_arquivo(caminho, sha256) -> list[Pagina]
    es: ES
    destinos: list[Path]
    estacao: str
    pid: int
    hoje: date
    pasta_temp: Path
    limiar: float = 0.80
    versoes: dict = field(default_factory=dict)


@dataclass(frozen=True)
class _Unidade:
    origem: arvore.Origem
    resultado: Resultado
    confiancas: dict
    # chave de nome vista com mais de um nascimento -> os nascimentos
    homonimos: dict = field(default_factory=dict)


def _sha256(caminho: Path) -> str:
    resumo = hashlib.sha256()
    with open(caminho, "rb") as arquivo:
        while bloco := arquivo.read(1 << 20):
            resumo.update(bloco)
    return resumo.hexdigest()


def _nascimentos_dos_homonimos(documentos, candidatos, nascimentos, pacote) -> dict:
    """Os nomes que o item mostra com mais de um nascimento, com os nascimentos.

    São os do documento sem paciente que `atribuir` marcou como homônimo, ou
    que traz ele mesmo dois nascimentos. A forma exibida entra também: é por
    ela que se compara o nome dos outros itens. Cada nome leva os nascimentos
    de todos esses documentos do item: com dois pares de homônimos no mesmo
    item sobra nascimento, e o que sobra só deixa a escolha da pasta mais
    estrita.
    """
    chaves, lidas = set(), set()
    for doc in documentos:
        indices = {p.indice for p in doc.paginas}
        datas = {nascimentos[i] for i in indices if i in nascimentos}
        if doc.paciente is not None or ("paciente:homonimo" not in doc.motivos
                                        and len(datas) < 2):
            continue
        lidas |= datas
        nomes = [c.valor_impresso for c in candidatos if c.pagina in indices]
        chaves |= {identidade.chave(n) for n in nomes}
        if nomes:
            chaves.add(identidade.chave(identidade.forma_exibida(nomes, pacote=pacote)))
    return {chave: frozenset(lidas) for chave in chaves}


def entender(paginas: list[Pagina], sha256: str, pacote, hoje: date,
             limiar: float = 0.80) -> tuple[Resultado, dict, dict]:
    """Resultado do arquivo, por paciente a fração das páginas com nome lido e
    os nomes que ele mostra com mais de um nascimento, com os nascimentos."""
    candidatos = identidade.candidatos(paginas, pacote, limiar)
    nascimentos = {}
    for pagina in paginas:
        lido = identidade.nascimento([pagina], pacote, hoje)
        if lido:
            nascimentos[pagina.indice] = lido
    nomes_por_pagina: dict[int, str] = {}
    for c in candidatos:
        nomes_por_pagina.setdefault(c.pagina, c.chave)
    documentos = segmentar.segmentar(paginas, pacote, nomes_por_pagina)
    resultado = atribuicao.atribuir(documentos, candidatos, nascimentos, pacote)
    prontos = []
    for doc in resultado.documentos:
        categoria, motivo = classificar.classificar(doc, pacote)
        doc = replace(doc, categoria=categoria, motivos=doc.motivos + (motivo,))
        doc = replace(doc, data=datas.data_do_exame(doc, pacote, hoje, doc.nascimento))
        doc = limpar.limpar(doc, pacote)
        original = next((a.id for a in prontos if a.repetido_de is None
                         and a.paciente == doc.paciente
                         and repetidos.mesmo_exame(a, doc, pacote=pacote)), None)
        prontos.append(replace(doc, repetido_de=original))
    confiancas = {}
    for paciente in {d.paciente for d in prontos if d.paciente}:
        paginas_dele = {p.indice for d in prontos if d.paciente == paciente for p in d.paginas}
        com_nome = {c.pagina for c in candidatos} & paginas_dele
        confiancas[paciente] = len(com_nome) / len(paginas_dele) if paginas_dele else 0.0
    brancos = tuple(p.indice for p in paginas if p.em_branco)
    return (replace(resultado, arquivo_sha256=sha256, documentos=tuple(prontos),
                    paginas_em_branco=brancos), confiancas,
            _nascimentos_dos_homonimos(prontos, candidatos, nascimentos, pacote))


def _ler(copia: Path, nome: str, sha256: str, trabalho: Path, ctx: Contexto,
         desistir: bool = False) -> list[_Unidade]:
    if desistir:
        # O arquivo já derrubou o trabalhador: vai sem leitura, com o original.
        evento("arquivo_entregue_sem_leitura")
        origem = arvore.Origem(copia, nome, sha256, None, sha256)
        return [_Unidade(origem, Resultado(sha256, (), (), False, ilegivel="falha_na_leitura"), {})]
    try:
        itens, ignorados = recipientes.expandir(copia, trabalho / "itens")
    except Ilegivel as erro:
        evento("arquivo_ilegivel", motivo=erro.motivo)
        origem = arvore.Origem(copia, nome, sha256, None, sha256)
        return [_Unidade(origem, Resultado(sha256, (), (), False, ilegivel=erro.motivo), {})]
    if ignorados:
        evento("formatos_ignorados", quantidade=len(ignorados))
    # O leitor que carrega os modelos sob demanda os carrega antes do relógio.
    preparar = getattr(ctx.leitor, "preparar", None)
    if itens and preparar is not None:
        preparar()
    unidades = []
    for item in itens:
        item_sha = sha256 if item.caminho == copia else _sha256(item.caminho)
        origem = arvore.Origem(copia, nome, sha256, item, item_sha)
        inicio = time.perf_counter()
        try:
            paginas = ctx.leitor.ler_arquivo(item.caminho, item_sha)
        except Ilegivel as erro:
            # O item fica na origem da unidade: num arquivo misto é ele, e não o
            # recipiente inteiro, que vai para a pasta DESCONHECIDO.
            evento("item_ilegivel", motivo=erro.motivo, tipo=item.tipo)
            unidades.append(_Unidade(origem, Resultado(item_sha, (), (), False,
                                                       ilegivel=erro.motivo), {}))
            continue
        segundos = time.perf_counter() - inicio
        resultado, confiancas, homonimos = entender(paginas, item_sha, ctx.pacote, ctx.hoje,
                                                    ctx.limiar)
        evento("item_lido", tipo=item.tipo, paginas=len(paginas),
               segundos_por_pagina=round(segundos / max(1, len(paginas)), 2),
               documentos=len(resultado.documentos), pacientes=len(resultado.pacientes),
               desconhecidos=sum(1 for d in resultado.documentos if d.paciente is None),
               em_branco=len(resultado.paginas_em_branco))
        unidades.append(_Unidade(origem, resultado, confiancas, homonimos))
    return unidades


def _uma_pessoa_por_nome(unidades: list[_Unidade]) -> tuple[list[_Unidade], dict]:
    """Os nascimentos que o arquivo de origem inteiro mostra para cada chave de nome.

    O documento sem nascimento lido de um nome com mais de um nascimento no
    arquivo (homônimos em itens diferentes, ou num item só, que então ficaram
    sem paciente) não tem de quem ser: DESCONHECIDO. Com um nascimento só, a
    herança depende do destino (`_herdar_nascimento`).
    """
    lidos: dict[str, set] = {}
    homonimos: set[str] = set()
    for unidade in unidades:
        homonimos |= set(unidade.homonimos)
        for chave, datas in unidade.homonimos.items():
            lidos.setdefault(chave, set()).update(datas)
        for doc in unidade.resultado.documentos:
            if doc.paciente and doc.nascimento:
                lidos.setdefault(identidade.chave(doc.paciente), set()).add(doc.nascimento)
    novas = []
    for unidade in unidades:
        resultado = unidade.resultado
        docs, mudou = [], False
        for doc in resultado.documentos:
            chave = identidade.chave(doc.paciente) if doc.paciente else None
            if (doc.nascimento is None and chave
                    and (len(lidos.get(chave, ())) > 1 or chave in homonimos)):
                doc = replace(doc, paciente=None, motivos=doc.motivos + ("paciente:homonimo",))
                mudou = True
            docs.append(doc)
        if mudou:
            pacientes = tuple(dict.fromkeys(d.paciente for d in docs if d.paciente))
            resultado = replace(resultado, documentos=tuple(docs), pacientes=pacientes,
                                multi_paciente=len(pacientes) > 1)
            unidade = replace(unidade, resultado=resultado)
        novas.append(unidade)
    return novas, {chave: frozenset(datas) for chave, datas in lidos.items()}


def _herdar_nascimento(unidades: list[_Unidade], nascimentos: dict, destino: Path,
                       pacote, registradas: dict, retrato: registro.Retrato) -> list[_Unidade]:
    """O documento sem nascimento lido herda o único nascimento lido para o nome no arquivo.

    Não herda quando o destino já tem homônimo registrado de outro nascimento:
    o documento pode ser de qualquer das pessoas com esse nome, e vai para
    pasta própria sem nascimento (`registro.achar_pasta`). O que uma tentativa
    anterior já entregou (`registradas`) numa pasta que é do nome lido agora
    mantém a decisão daquela entrega: herda só se essa pasta tem o nascimento
    lido.
    """
    def compativeis(a: str, b: str) -> bool:
        return identidade.compativeis(a, b, pacote=pacote)

    homonimo: dict[str, bool] = {}
    novas = []
    for unidade in unidades:
        caminho = arvore.caminho_de_origem(unidade.origem)
        docs, mudou = [], False
        for doc in unidade.resultado.documentos:
            datas = nascimentos.get(identidade.chave(doc.paciente), ()) if doc.paciente else ()
            if doc.nascimento is None and len(datas) == 1:
                [lido] = datas
                entregue = registradas.get((caminho, tuple(p.indice for p in doc.paginas)))
                if entregue is not None and registro.e_da_pessoa(
                        entregue[0], doc.paciente, lido, compativeis, identidade.chave, retrato):
                    herda = entregue[1] == lido.isoformat()
                else:
                    if doc.paciente not in homonimo:
                        homonimo[doc.paciente] = registro.tem_homonimo(
                            destino, doc.paciente, lido, compativeis, identidade.chave, retrato)
                    herda = not homonimo[doc.paciente]
                if herda:
                    doc, mudou = replace(doc, nascimento=lido), True
            docs.append(doc)
        if mudou:
            unidade = replace(unidade, resultado=replace(unidade.resultado,
                                                          documentos=tuple(docs)))
        novas.append(unidade)
    return novas


def processar(arquivo: Path, ctx: Contexto, desistir: bool = False) -> dict:
    """Processa um arquivo de origem em todos os destinos que ainda não o têm.

    Com `desistir`, o arquivo não é lido: vai para DESCONHECIDO, com o
    original e o LEIA-ME de falha na leitura.
    """
    inicio = time.perf_counter()
    ctx.pasta_temp.mkdir(parents=True, exist_ok=True)
    trabalho = Path(tempfile.mkdtemp(prefix="stt-", dir=ctx.pasta_temp))
    try:
        copia, sha256 = ctx.es.copiar_para_temp(Path(arquivo), trabalho)
        pendentes = [d for d in ctx.destinos if not registro.ja_feito(d, sha256)]
        if not pendentes:
            return {"sha256": sha256, "situacao": "ja_feito"}
        # Fora da pasta de trabalho, que é apagada no fim: a observação de trava
        # alheia vale para o próximo arquivo e para o trabalhador seguinte.
        observadas = arquivo_de_observacoes(ctx.pasta_temp / OBSERVADAS)
        travas = [Trava(d, sha256, ctx.estacao, ctx.pid, observadas=observadas)
                  for d in pendentes]
        minhas = [t for t in travas if t.tomar()]
        if not minhas:
            return {"sha256": sha256, "situacao": "em_outra_estacao"}
        try:
            with Batimento(minhas):
                unidades = _ler(copia, Path(arquivo).name, sha256, trabalho, ctx, desistir)
                unidades, nascimentos = _uma_pessoa_por_nome(unidades)
                gravador = arvore.Gravador(ctx.pacote, ctx.es, ctx.estacao, ctx.pid, ctx.hoje,
                                           ctx.limiar, ctx.versoes, trabalho, observadas)
                gravados = ocupados = 0
                for trava in minhas:
                    # Outra estação pode ter tomado a trava durante o OCR: quem
                    # perdeu não publica nada naquele destino.
                    if not trava.e_minha():
                        evento("trava_perdida")
                        continue
                    try:
                        with arvore.escrita_exclusiva(trava.destino, gravador) as exclusivo:
                            retrato = registro.Retrato(trava.destino, sha256)
                            registradas = registro.paginas_registradas(trava.destino, sha256,
                                                                       retrato)
                            aqui = _herdar_nascimento(unidades, nascimentos, trava.destino,
                                                      ctx.pacote, registradas, retrato)
                            pastas = arvore.escrever_arquivo(
                                [(u.resultado, u.origem, u.confiancas) for u in aqui],
                                trava.destino, exclusivo, nascimentos, registradas, retrato)
                            exclusivo.conferir_escrita()
                            registro.marcar_feito(trava.destino, sha256, sorted(set(pastas)),
                                                  ctx.es, ctx.estacao, ctx.pid)
                    except arvore.DestinoOcupado:
                        # Não é defeito do arquivo: nada foi gravado neste
                        # destino, e o arquivo volta inteiro no próximo ping.
                        ocupados += 1
                        continue
                    gravados += 1
        finally:
            for trava in minhas:
                trava.soltar()
        if ocupados:
            return {"sha256": sha256, "situacao": "destino_ocupado"}
        if not gravados:
            return {"sha256": sha256, "situacao": "trava_perdida"}
        resumo = {"sha256": sha256, "situacao": "feito", "destinos": gravados,
                  "documentos": sum(len(u.resultado.documentos) for u in unidades),
                  "ilegiveis": sum(1 for u in unidades if u.resultado.ilegivel),
                  "desconhecidos": sum(1 for u in unidades for d in u.resultado.documentos
                                       if d.paciente is None),
                  "segundos": round(time.perf_counter() - inicio, 1)}
        evento("arquivo_processado", **resumo)
        return resumo
    finally:
        shutil.rmtree(trabalho, ignore_errors=True)
