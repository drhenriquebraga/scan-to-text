"""Markdown do documento e do COMPLETO.md.

O serializador é próprio porque o do Docling escapa `_` e `*`. Aqui o texto
sai literal, sem escape nenhum: é para copiar e colar, e a literalidade vale
mais que a renderização perfeita. Um `|` dentro de célula fica como veio,
mesmo que desalinhe a tabela renderizada.

Toda linha de campo, de cabeçalho original e de corpo termina com dois
espaços (quebra de linha do Markdown): renderizada, cada linha impressa fica
na sua linha; copiada, sai limpa. Linhas do mesmo grupo do layout formam um
parágrafo; grupos diferentes são separados por uma linha em branco.

Nada aqui escreve nome de arquivo de origem nem data de transcrição: o
Documento não os carrega, e eles moram no manifesto.
"""
from __future__ import annotations

from collections.abc import Sequence

from scan_to_text.entender import datas
from scan_to_text.escrever.nomes import nome_documento, prefixo_data
from scan_to_text.pacote import Pacote
from scan_to_text.tipos import Bloco, DataExame, Documento, Linha, Tabela

QUEBRA = "  "
LIMIAR = 0.80


def _uma_linha(texto: str | None) -> str:
    return " ".join((texto or "").split())


def _linhas(texto: str) -> list[str]:
    """As linhas não vazias de um texto, aparadas nas pontas."""
    return [parte.strip() for parte in texto.splitlines() if parte.strip()]


def _celula(texto: str) -> str:
    # Quebra dentro de célula ou de item de lista desmontaria a linha do
    # Markdown; vira espaço.
    return " ".join(_linhas(texto))


def _tem_digito(texto: str) -> bool:
    return any(c.isdigit() for c in texto)


def _titulo(doc: Documento, pacote: Pacote) -> str:
    return _uma_linha(doc.titulo) or pacote["textos"]["documento"]["sem_titulo"]


def _data_impressa(data: DataExame, pacote: Pacote) -> str:
    """A data do exame como o documento a imprime.

    O trecho vai do rótulo ao fim da data, e só tem essa data: ela é achada de
    novo nele com as regras de data do pacote. Sem trecho, sem regras de data
    no pacote ou sem achar o mesmo valor, sai no formato do pacote.
    """
    precisao = data.precisao or "dia"
    trecho = _uma_linha(data.trecho)
    if trecho and "datas" in pacote.secoes:
        impressas = [trecho[inicio:fim]
                     for valor, achada, inicio, fim in datas.datas_no_texto(trecho, pacote)
                     if valor == data.valor and achada == precisao]
        if impressas:
            return impressas[-1]
    formatos = pacote["textos"]["markdown"]["formato_data"]
    if precisao not in formatos:
        raise ValueError("precisao_desconhecida")
    return data.valor.strftime(formatos[precisao])


def _campos(doc: Documento, pacote: Pacote) -> list[str]:
    md = pacote["textos"]["markdown"]
    valores = [(md["paciente"], _uma_linha(doc.paciente)),
               (md["data_exame"],
                _data_impressa(doc.data, pacote) if doc.data.valor is not None else ""),
               (md["instituicao"], _uma_linha(doc.instituicao))]
    return [md["campo"].format(rotulo=rotulo, valor=valor) + QUEBRA
            for rotulo, valor in valores if valor]


def _cabecalho(doc: Documento) -> list[str]:
    # Linha igual à instituição ou ao título já está no bloco de identificação.
    ja_ditas = {_uma_linha(doc.instituicao), _uma_linha(doc.titulo)} - {""}
    linhas, vistas = [], set()
    for texto in (parte for linha in doc.cabecalho_original for parte in _linhas(linha)):
        if texto in vistas or _uma_linha(texto) in ja_ditas:
            continue
        vistas.add(texto)
        linhas.append(texto + QUEBRA)
    return linhas


def _tabela(tabela: Tabela) -> str:
    linhas = [[_celula(c) for c in linha] for linha in tabela.celulas]
    largura = max((len(linha) for linha in linhas), default=0)
    if not largura:
        return ""
    linhas = [linha + [""] * (largura - len(linha)) for linha in linhas]
    # A primeira linha vira o cabeçalho da tabela Markdown, que exige um; o
    # texto é o mesmo, só o negrito da renderização muda.
    saida = [linhas[0], ["---"] * largura, *linhas[1:]]
    return "\n".join("| " + " | ".join(linha) + " |" for linha in saida)


def _corpo(blocos: Sequence[Bloco]) -> list[str]:
    paragrafos: list[str] = []
    atual: list[str] = []
    grupo = None

    def fechar() -> None:
        if atual:
            paragrafos.append("\n".join(atual))
            atual.clear()

    for bloco in blocos:
        if isinstance(bloco, Tabela):
            fechar()
            tabela = _tabela(bloco)
            if tabela:
                paragrafos.append(tabela)
            continue
        textos = _linhas(bloco.texto)
        if not textos:
            continue
        if atual and bloco.grupo != grupo:
            fechar()
        atual.extend(texto + QUEBRA for texto in textos)
        grupo = bloco.grupo
    fechar()
    return paragrafos


def _le_o_trecho(trecho: str, texto: str, acima: str | None = None,
                 esquerda: str | None = None) -> bool:
    """`texto` é onde `datas.data_do_exame` leu a data do `trecho`.

    O trecho vai do rótulo ao fim da data e está numa linha ou célula; numa
    célula com a da esquerda, separadas por " | "; ou é a linha (ou célula) de
    cima inteira, um espaço e a data que abre esta.
    """
    proprio = _uma_linha(texto)
    if trecho in proprio:
        return True
    if esquerda is not None and trecho not in _uma_linha(esquerda):
        if trecho in _uma_linha(f"{esquerda} | {texto}"):
            return True
    cima = _uma_linha(acima)
    return bool(cima) and trecho.startswith(cima + " ") and trecho[len(cima) + 1:] in proprio


def _leituras_da_data(doc: Documento) -> list[tuple[list[str], float]]:
    """Onde a data do exame foi lida: (linhas como saíram, confiança).

    Procura em todas as camadas das páginas, como a data foi achada: a
    leitura pode estar num rodapé ou noutro trecho que a limpeza tirou, e o
    número continua no cabeçalho de identificação e no nome do arquivo. A
    vizinhança (linha de cima, célula de cima e da esquerda) é a mesma de
    `datas.data_do_exame`.
    """
    trecho = _uma_linha(doc.data.trecho)
    leituras: list[tuple[list[str], float]] = []
    for pagina in doc.paginas:
        anterior = None
        for bloco in pagina.blocos:
            if isinstance(bloco, Linha):
                if _le_o_trecho(trecho, bloco.texto, acima=anterior):
                    leituras.append((_linhas(bloco.texto), bloco.confianca))
                anterior = bloco.texto
                continue
            anterior = None
            for i, linha in enumerate(bloco.celulas):
                confiancas = bloco.confiancas[i] if i < len(bloco.confiancas) else ()
                for j, celula in enumerate(linha):
                    acima = next((bloco.celulas[k][j] for k in range(i - 1, -1, -1)
                                  if j < len(bloco.celulas[k]) and bloco.celulas[k][j].strip()),
                                 None)
                    if _le_o_trecho(trecho, celula, acima=acima,
                                    esquerda=linha[j - 1] if j else None):
                        leituras.append(([" | ".join(_celula(c) for c in linha)],
                                         confiancas[j] if j < len(confiancas) else 0.0))
    return leituras


def linhas_incertas(doc: Documento, limiar: float = LIMIAR) -> list[str]:
    """Linhas (e linhas de tabela) com algum dígito lido abaixo do limiar.

    A confiança do OCR é por linha e por célula: a linha entra quando tem
    dígito e confiança abaixo do limiar. Célula sem confiança registrada conta
    como incerta. As linhas do cabeçalho original, que chegam como texto,
    buscam a confiança nas páginas do documento.

    A data do exame vem primeiro, porque está no cabeçalho de identificação:
    entra a leitura de onde ela saiu, em qualquer camada, quando lida abaixo
    do limiar. Leitura que não se acha nas páginas conta como incerta, e entra
    o trecho da data. Sem trecho, a data não veio da leitura e não há linha a
    listar.
    """
    achadas: list[str] = []

    def incluir(texto: str) -> None:
        if texto not in achadas:
            achadas.append(texto)

    if doc.data.valor is not None and _uma_linha(doc.data.trecho):
        leituras = _leituras_da_data(doc)
        if not leituras:
            incluir(_uma_linha(doc.data.trecho))
        for linhas, confianca in leituras:
            if confianca < limiar:
                for parte in linhas:
                    if _tem_digito(parte):
                        incluir(parte)

    cabecalho = [parte for linha in doc.cabecalho_original for parte in _linhas(linha)]
    if cabecalho:
        incertas = {parte
                    for pagina in doc.paginas for bloco in pagina.blocos
                    if isinstance(bloco, Linha) and bloco.confianca < limiar
                    for parte in _linhas(bloco.texto) if _tem_digito(parte)}
        for texto in cabecalho:
            if texto in incertas:
                incluir(texto)

    for bloco in doc.corpo:
        if isinstance(bloco, Linha):
            # Cada linha física, como sai no corpo; juntá-las daria uma linha
            # que não está no texto.
            if bloco.confianca < limiar:
                for parte in _linhas(bloco.texto):
                    if _tem_digito(parte):
                        incluir(parte)
            continue
        for i, linha in enumerate(bloco.celulas):
            confiancas = bloco.confiancas[i] if i < len(bloco.confiancas) else ()
            if any(_tem_digito(celula)
                   and (confiancas[j] if j < len(confiancas) else 0.0) < limiar
                   for j, celula in enumerate(linha)):
                incluir(" | ".join(_celula(c) for c in linha))
    return achadas


def _lista_incerta(doc: Documento, pacote: Pacote, limiar: float) -> str | None:
    linhas = linhas_incertas(doc, limiar)
    if not linhas:
        return None
    rotulo = pacote["textos"]["markdown"]["leitura_incerta"]
    return rotulo + "\n" + "\n".join(f"- {linha}" for linha in linhas)


def _miolo(doc: Documento, pacote: Pacote) -> list[str]:
    """Bloco de identificação sem o título, cabeçalho original e corpo."""
    partes = []
    for bloco in (_campos(doc, pacote), _cabecalho(doc)):
        if bloco:
            partes.append("\n".join(bloco))
    partes.extend(_corpo(doc.corpo))
    return partes


def md_documento(doc: Documento, pacote: Pacote, limiar: float = LIMIAR) -> str:
    """O MD de um documento, terminado pelo aviso de números."""
    partes = [f"# {_titulo(doc, pacote)}", *_miolo(doc, pacote),
              "## " + pacote["textos"]["markdown"]["aviso"]]
    lista = _lista_incerta(doc, pacote, limiar)
    if lista:
        partes.append(lista)
    return "\n\n".join(partes) + "\n"


def md_completo(nome_exibido: str, documentos: Sequence[Documento], pacote: Pacote,
                limiar: float = LIMIAR) -> str:
    """O COMPLETO.md de um paciente.

    Categoria 1 a 5 e data crescente dentro de cada uma, sem data no fim; o
    desempate é o nome do arquivo e depois o id, para a saída não depender da
    ordem de entrada. O aviso de números sai uma vez, no topo, e cada
    documento traz a própria lista. Repetido é transcrito uma vez só:
    sai a cópia cujo original também está em `documentos`.
    """
    md = pacote["textos"]["markdown"]
    separador = md["separador"]

    def leitura(categoria: int) -> str:
        if categoria not in md["categorias"]:
            raise ValueError("categoria_invalida")
        return md["categorias"][categoria]

    def ordem(doc: Documento) -> tuple:
        return (doc.categoria, doc.data.valor is None,
                nome_documento(doc.data, doc.titulo, pacote), doc.id)

    # Repetido só sai quando o original está na lista: senão a cópia é a única
    # transcrição daquele exame aqui.
    presentes = {d.id for d in documentos}
    docs = sorted((d for d in documentos if d.repetido_de not in presentes), key=ordem)
    categorias: dict[int, list[Documento]] = {}
    for doc in docs:
        categorias.setdefault(doc.categoria, []).append(doc)

    partes = [f"# {_uma_linha(nome_exibido)}", f"## {md['aviso']}", f"## {md['indice']}"]
    for categoria, lista in categorias.items():
        itens = [f"- {prefixo_data(d.data, pacote)}{separador}{_titulo(d, pacote)}" for d in lista]
        partes.append(f"**{leitura(categoria)}**\n" + "\n".join(itens))
    partes.append("---")
    for categoria, lista in categorias.items():
        partes.append(f"## {leitura(categoria)}")
        for doc in lista:
            partes.append(f"### {_titulo(doc, pacote)}{separador}{prefixo_data(doc.data, pacote)}")
            partes.extend(_miolo(doc, pacote))
            incerta = _lista_incerta(doc, pacote, limiar)
            if incerta:
                partes.append(incerta)
            partes.append("---")
    return "\n\n".join(partes) + "\n"
