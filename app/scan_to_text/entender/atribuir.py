"""A quem pertence cada documento de um arquivo.

- Documento com nome aceito vai para esse paciente; nomes compatíveis do mesmo
  arquivo são o mesmo paciente, exibido pela variante mais completa.
- Nome civil nomeia; só o social também conta; se algum documento traz os
  dois, o que só tem o social vai para a pasta do civil.
- Documento sem nome herda o paciente só quando o arquivo tem exatamente um e
  nenhuma leitura do arquivo aponta outra pessoa.
- Conflito no documento, nome ambíguo (compatível com duas pessoas),
  homônimo (datas de nascimento diferentes) e dúvida deixam o paciente vazio:
  a pasta DESCONHECIDO, com o MD entregue. Contaminação nunca vai para a pasta
  do primeiro paciente. Conflito inclui a dúvida e o nome válido lido embaixo
  de rótulo de paciente em linha de duas colunas, quando incompatíveis com o
  nome aceito do documento.

O motivo de cada decisão entra em `Documento.motivos` como "paciente:<codigo>",
para o manifesto.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date

from scan_to_text.entender import identidade
from scan_to_text.entender.identidade import SOCIAL, Leitura
from scan_to_text.pacote import Pacote
from scan_to_text.tipos import Candidato, Documento, Resultado

Compat = Callable[[str, str], bool]


@dataclass
class _Ficha:
    civis: list[str]
    sociais: list[str]
    duvidas: list[Leitura]
    reservas: list[Leitura]
    nascimentos: set[date]
    estado: str                 # "nomeado", "conflito", "duvida" ou "sem_nome"
    grupo: int | None = None
    motivo: str = ""


@dataclass
class _Grupo:
    nomes: list[str] = field(default_factory=list)      # nomeiam a pasta
    sociais: list[str] = field(default_factory=list)    # nomes sociais ligados
    fichas: list[int] = field(default_factory=list)
    nascimento: date | None = None
    homonimo: bool = False


def _coerentes(nomes: Sequence[str], compat: Compat) -> bool:
    return all(compat(a, b) for i, a in enumerate(nomes) for b in nomes[i + 1:])


def _duvida_contraria(leitura: Leitura, civis: list[str], sociais: list[str],
                      compat: Compat) -> bool:
    mesmos = sociais if leitura.rotulo == SOCIAL else civis
    if mesmos:
        return not all(compat(leitura.valor, n) for n in mesmos)
    # Civil duvidoso num documento que só tem o social aceito: o civil nomearia
    # a pasta, e não se sabe de quem é.
    return leitura.rotulo != SOCIAL


def _ficha(doc: Documento, por_pagina: Mapping[int, list[Candidato]],
           aceitos: set[tuple[int, str]], nascimentos: Mapping[int, date],
           pacote: Pacote, compat: Compat) -> _Ficha:
    indices = [p.indice for p in doc.paginas]
    cands = [c for i in indices for c in por_pagina.get(i, ())]
    civis = [c.valor_impresso for c in cands if c.rotulo != SOCIAL]
    sociais = [c.valor_impresso for c in cands if c.rotulo == SOCIAL]
    duvidas, reservas = [], []
    for leitura in identidade.leituras(doc.paginas, pacote):
        if leitura.efeito == "reserva":
            reservas.append(leitura)
        elif leitura.efeito == "duvida" or (leitura.pagina, leitura.chave) not in aceitos:
            # Nome válido que não virou candidato (confiança abaixo do limiar de
            # quem chamou) é dúvida, nunca documento sem nome.
            duvidas.append(leitura)
    dns = {nascimentos[i] for i in indices if i in nascimentos}
    # Nome válido embaixo de rótulo de paciente, numa linha com rótulo de outra
    # pessoa, pode ser o paciente desta página: conta como dúvida. As outras
    # reservas não contam aqui: valor não lido não aponta ninguém, e rótulo
    # genérico ao lado de médico ou conselho costuma ser a assinatura.
    contra = duvidas + [r for r in reservas if r.motivo == "duas_colunas"
                        and identidade.validar(r.valor, pacote=pacote)[0]]
    if not _coerentes(civis, compat) or not _coerentes(sociais, compat) or len(dns) > 1:
        estado = "conflito"
    elif civis or sociais:
        contraria = any(_duvida_contraria(d, civis, sociais, compat) for d in contra)
        estado = "conflito" if contraria else "nomeado"
    elif duvidas or reservas:
        estado = "duvida"
    else:
        estado = "sem_nome"
    return _Ficha(civis, sociais, duvidas, reservas, dns, estado)


def _agrupar(nomes: Sequence[str], compat: Compat) -> dict[str, int]:
    """Grupo de cada chave de nome. Nome compatível com dois nomes
    incompatíveis entre si é ambíguo e fica fora; sem os ambíguos, a
    compatibilidade é transitiva e cada grupo é uma pessoa."""
    chaves = list(dict.fromkeys(identidade.chave(n) for n in nomes))
    comp = {(a, b): a == b or compat(a, b) for a in chaves for b in chaves}
    ambiguos = {k for k in chaves
                if any(comp[x, k] and comp[y, k] and not comp[x, y]
                       for x in chaves for y in chaves)}
    grupo: dict[str, int] = {}
    for k in chaves:
        if k in ambiguos:
            continue
        existente = next((grupo[o] for o in grupo if comp[o, k]), None)
        grupo[k] = len(set(grupo.values())) if existente is None else existente
    return grupo


def _grupo_da_ficha(nomes: Sequence[str], grupo_local: dict[str, int]) -> int | None:
    ids = {grupo_local[k] for k in map(identidade.chave, nomes) if k in grupo_local}
    return ids.pop() if len(ids) == 1 else None


def _formar_grupos(fichas: list[_Ficha], compat: Compat) -> list[_Grupo]:
    grupos: list[_Grupo] = []

    def assentar(i: int, local: int, globais: dict[int, int], nomes: list[str], motivo: str):
        g = globais.setdefault(local, len(grupos))
        if g == len(grupos):
            grupos.append(_Grupo())
        fichas[i].grupo, fichas[i].motivo = g, motivo
        grupos[g].fichas.append(i)
        grupos[g].nomes.extend(nomes)
        grupos[g].sociais.extend(fichas[i].sociais)

    nomeados = [i for i, f in enumerate(fichas) if f.estado == "nomeado"]
    civis = _agrupar([n for i in nomeados for n in fichas[i].civis], compat)
    globais_civis: dict[int, int] = {}
    for i in nomeados:
        if fichas[i].civis:
            local = _grupo_da_ficha(fichas[i].civis, civis)
            if local is None:
                fichas[i].estado = "ambiguo"
            else:
                assentar(i, local, globais_civis, fichas[i].civis, "rotulo")

    so_sociais = []
    for i in nomeados:
        f = fichas[i]
        if f.civis:
            continue
        alvos = {g for g, grupo in enumerate(grupos)
                 if any(compat(s, n) for s in f.sociais for n in grupo.sociais + grupo.nomes)}
        if len(alvos) == 1:
            g = alvos.pop()
            f.grupo, f.motivo = g, "social"
            grupos[g].fichas.append(i)
            grupos[g].sociais.extend(f.sociais)
        elif alvos:
            f.estado = "ambiguo"
        else:
            so_sociais.append(i)

    sociais = _agrupar([n for i in so_sociais for n in fichas[i].sociais], compat)
    globais_sociais: dict[int, int] = {}
    for i in so_sociais:
        local = _grupo_da_ficha(fichas[i].sociais, sociais)
        if local is None:
            fichas[i].estado = "ambiguo"
        else:
            assentar(i, local, globais_sociais, fichas[i].sociais, "rotulo")

    for grupo in grupos:
        dns = set().union(*(fichas[i].nascimentos for i in grupo.fichas))
        if len(dns) > 1:
            grupo.homonimo = True
            for i in grupo.fichas:
                fichas[i].estado, fichas[i].motivo = "homonimo", "homonimo"
        else:
            grupo.nascimento = next(iter(dns), None)
    return grupos


def _outros_nomes(fichas: list[_Ficha], pacote: Pacote) -> list[str]:
    """Tudo no arquivo que pode ser nome de alguém além dos pacientes aceitos."""
    outros = []
    for f in fichas:
        if f.estado in ("conflito", "ambiguo", "homonimo"):
            outros += f.civis + f.sociais
        outros += [d.valor for d in f.duvidas]
        outros += [r.valor for r in f.reservas
                   if r.motivo == "sem_valor" or identidade.validar(r.valor, pacote=pacote)[0]]
    return outros


def atribuir(documentos: Sequence[Documento], candidatos: Sequence[Candidato],
             nascimentos: Mapping[int, date], pacote: Pacote) -> Resultado:
    """Paciente de cada documento.

    `candidatos` são os de `identidade.candidatos` sobre as páginas do arquivo;
    `nascimentos` leva o índice da página à data de nascimento lida nela."""
    docs = tuple(documentos)
    if not docs:
        return Resultado(arquivo_sha256="", documentos=(), pacientes=(), multi_paciente=False)

    def compat(a: str, b: str) -> bool:
        return identidade.compativeis(a, b, pacote=pacote)

    por_pagina: dict[int, list[Candidato]] = {}
    for c in candidatos:
        por_pagina.setdefault(c.pagina, []).append(c)
    aceitos = {(c.pagina, c.chave) for c in candidatos}
    fichas = [_ficha(doc, por_pagina, aceitos, nascimentos, pacote, compat) for doc in docs]
    grupos = _formar_grupos(fichas, compat)

    resolvidos = sorted((g for g, grupo in enumerate(grupos) if not grupo.homonimo),
                        key=lambda g: min(grupos[g].fichas))
    unico = grupos[resolvidos[0]] if len(resolvidos) == 1 else None
    livre = unico is not None and all(
        any(compat(o, v) for v in unico.nomes + unico.sociais)
        for o in _outros_nomes(fichas, pacote))
    for f in fichas:
        if f.estado != "sem_nome":
            continue
        if unico is None:
            f.motivo = "varios" if resolvidos else "sem_nome"
        elif not livre:
            f.motivo = "bloqueado"
        elif f.nascimentos and unico.nascimento and f.nascimentos != {unico.nascimento}:
            f.motivo = "nascimento"
        else:
            f.grupo, f.motivo = resolvidos[0], "herdado"

    exibidos = {g: identidade.forma_exibida(grupos[g].nomes, pacote=pacote) for g in resolvidos}
    novos = []
    for doc, f in zip(docs, fichas):
        grupo = grupos[f.grupo] if f.grupo is not None else None
        valido = grupo is not None and not grupo.homonimo
        novos.append(replace(
            doc,
            paciente=exibidos[f.grupo] if valido else None,
            nascimento=grupo.nascimento if valido and grupo.nascimento else doc.nascimento,
            motivos=doc.motivos + (f"paciente:{f.motivo or f.estado}",)))
    pacientes = tuple(exibidos[g] for g in resolvidos)
    sha = docs[0].paginas[0].arquivo_sha256 if docs[0].paginas else ""
    return Resultado(arquivo_sha256=sha, documentos=tuple(novos), pacientes=pacientes,
                     multi_paciente=len(pacientes) > 1)
