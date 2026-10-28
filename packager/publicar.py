"""Monta a cópia pública do Scan-to-Text.

A cópia é a única coisa que vai ao GitHub, e é a fonte do zip do release
(`montar_pacote.py`). Ela nasce em
`%LOCALAPPDATA%/RADIOTERAPIA_AI_PUBLICACAO/scan-to-text/`, ou em
`<RADIOTERAPIA_AI_PUBLICACAO>/scan-to-text/`, fora do Drive de propósito: é
gerada e se reconstrói da oficina.

  1. O destino é recusado se cair no Drive (ou outra pasta sincronizada), na
     raiz de dado de paciente e de instalação dos apps, no projeto ou em pasta
     que um app escreve.
  2. Só entra arquivo do último commit da oficina (HEAD) permitido por
     `permitidos.txt`, com o conteúdo do commit: mudança não commitada e
     arquivo só no índice não passaram pelo gancho de pré-commit nem pela
     revisão. O que mora em `publico/` (README em inglês, LICENSE, NOTICE...)
     vai para a raiz da cópia.
  3. A varredura roda NA CÓPIA, nunca na oficina: o que se publica é o que foi
     varrido. Procura dado de paciente (`tools/checar_paciente.py`), segredo,
     caminho absoluto da oficina ou do perfil de quem publica, extensão
     proibida e comentário com data dd/mm/aaaa. Nunca imprime o valor achado: diz arquivo,
     linha e regra, e o caminho de achado de paciente sai como hash.
  4. Só a cópia varrida sem achado e com o piso do repositório público recebe
     o selo `.varredura-ok`, com o SHA-256 dos caminhos e do conteúdo dela. O
     montador (`montar_pacote.py`) e o `--commit` exigem o selo conferido.
     Com achado, sem o piso, ou com a montagem parada por qualquer erro
     (arquivo preso, Ctrl+C), a cópia é esvaziada (menos o `.git`) e fica sem
     selo.
  5. `--commit` faz um commit por versão na cópia, com o selo fora do Git.
     Não há push nem remote: o envio é um passo à parte, fora deste script.

Uso:
    python packager/publicar.py            monta e confere
    python packager/publicar.py --commit   monta, confere e faz o commit na cópia
"""
import ast
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tokenize
import tomllib
import warnings
from pathlib import Path, PurePosixPath

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "tools"))
sys.path.insert(0, str(RAIZ / "app"))

import checar_paciente  # noqa: E402

REPO = "scan-to-text"
VARIAVEL = "RADIOTERAPIA_AI_PUBLICACAO"
PERMITIDOS = Path(__file__).with_name("permitidos.txt")
RAIZ_PUBLICA = "publico/"
MARCA = ".varredura-ok"
PISO = ("README.md", "LICENSE", "NOTICE", ".gitattributes", ".gitignore")
EXTENSOES_PROIBIDAS = {".pem", ".key", ".pfx", ".p12", ".jks", ".keystore", ".env", ".csv",
                       ".xls", ".xlsx", ".xlsb", ".dcm", ".sqlite", ".db"}

# As expressões não casam com o próprio texto: a barra dobrada do código-fonte
# não é o caminho que elas procuram.
REGRAS = (
    ("caminho da oficina", re.compile(r"\b[A-Za-z]:[\\/]+AI_(?:PROJETOS|DEPLOY)\b", re.I)),
    ("chave privada", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
                         r"|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}"
                         r"|hf_[A-Za-z0-9]{30,}|AIza[0-9A-Za-z_-]{35})")),
    ("senha em texto", re.compile(r"\b(?:password|passwd|senha|secret|api[_-]?key)\s*[:=]\s*"
                                  r"[\"'][^\"'\s]{4,}[\"']", re.I)),
)
DATA = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
# No .cmd, o comentário também vem depois de `&`, `&&` ou `||` na mesma linha.
_COMENTARIO_CMD = re.compile(r"(?im)(?:^|[&|])[ \t]*(?:@?rem\b|::).*$")
_COMENTARIO_CERQUILHA = re.compile(r"(?m)(?:^|(?<=\s))#.*$")
_COMENTARIO_BLOCO = re.compile(r"/\*.*?\*/", re.S)
_COMENTARIO_BARRAS = re.compile(r"(?m)(?<![:\"'\\\w])//.*$")
_COMENTARIO_HTML = re.compile(r"<!--.*?-->", re.S)
_CERQUILHA = {"", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".txt", ".sh"}


class PublicacaoRecusada(Exception):
    pass


# ---------------------------------------------------------------- destino

def destino_padrao(ambiente=os.environ) -> Path:
    base = (ambiente.get(VARIAVEL) or "").strip().strip('"')
    if not base:
        base = Path(ambiente.get("LOCALAPPDATA") or Path.home()) / "RADIOTERAPIA_AI_PUBLICACAO"
    return Path(base) / REPO


def _normal(caminho: Path) -> str:
    return os.path.normcase(os.path.abspath(caminho)).rstrip("\\/")


def _dentro(caminho: Path, pasta: Path) -> bool:
    c, p = _normal(caminho), _normal(pasta)
    return c == p or c.startswith(p + os.sep)


def problemas_de_destino(destino: Path, oficina: Path = RAIZ, ambiente=os.environ,
                         em_nuvem=None) -> list[str]:
    if em_nuvem is None:
        from scan_to_text.nuvem import em_pasta_sincronizada as em_nuvem
    local = Path(ambiente.get("LOCALAPPDATA") or Path.home())
    proibidas = (
        (oficina, "a oficina (o proprio projeto)"),
        (Path((ambiente.get("SystemDrive") or "C:") + "\\") / "RADIOTERAPIA_AI",
         "raiz de dado de paciente e de instalacao dos apps"),
        (local / "Scan-to-Text", "o Scan-to-Text escreve nela"),
        (local / "RADIOTERAPIA_AI", "um app escreve nela"),
        (local / "RadioterapiaAI", "um app escreve nela"),
        (local / "RadAI_Watcher", "um app escreve nela"),
    )
    problemas = [f"{destino} fica em {pasta}: {razao}"
                 for pasta, razao in proibidas if _dentro(destino, pasta)]
    if em_nuvem(Path(destino)):
        problemas.append(f"{destino} fica numa pasta sincronizada com a nuvem")
    return problemas


# ---------------------------------------------------------------- lista de permitidos

def ler_permitidos(caminho: Path = PERMITIDOS) -> list[str]:
    entradas = []
    for linha in caminho.read_text(encoding="utf-8-sig").splitlines():
        entrada = linha.strip().replace("\\", "/")
        if not entrada or entrada.startswith("#"):
            continue
        partes = PurePosixPath(entrada).parts
        if entrada.startswith("/") or ":" in entrada or ".." in partes:
            raise PublicacaoRecusada(f"permitidos.txt: entrada fora do projeto: {entrada}")
        entradas.append(entrada)
    return entradas


def permitido(caminho: str, entradas) -> bool:
    return any(caminho == e or (e.endswith("/") and caminho.startswith(e)) for e in entradas)


def arquivos_do_commit(oficina: Path) -> dict[str, bytes]:
    """{caminho: conteúdo} do último commit da oficina, sem olhar o índice nem o disco.

    O `git archive` aplica os atributos de fim de linha como um checkout: o
    blob do `.cmd` é LF, e o `INSTALL.cmd` da cópia tem de sair em CRLF.
    """
    r = subprocess.run(["git", "-C", str(oficina), "archive", "--format=tar", "HEAD"],
                       capture_output=True)
    if r.returncode != 0:
        raise PublicacaoRecusada("nao consegui ler o ultimo commit da oficina (git archive HEAD): "
                                 "a publicacao sai de um commit")
    arquivos = {}
    with tarfile.open(fileobj=io.BytesIO(r.stdout), encoding="utf-8") as tar:
        for membro in tar:
            if membro.isfile():
                arquivos[membro.name] = tar.extractfile(membro).read()
    return arquivos


def selecionar(versionados, entradas) -> dict[str, str]:
    """{caminho na cópia: caminho na oficina}; o que vem de `publico/` vence."""
    copias = {rel: rel for rel in versionados if permitido(rel, entradas)}
    for rel in versionados:
        if rel.startswith(RAIZ_PUBLICA):
            copias[rel[len(RAIZ_PUBLICA):]] = rel
    return copias


# ---------------------------------------------------------------- varredura

def _trechos_py(texto: str) -> list[tuple[int, int]]:
    inicios = [0] + [m.end() for m in re.finditer("\n", texto)]
    trechos = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            for t in tokenize.generate_tokens(io.StringIO(texto).readline):
                if t.type == tokenize.COMMENT:
                    inicio = inicios[t.start[0] - 1] + t.start[1]
                    trechos.append((inicio, inicio + len(t.string)))
            arvore = ast.parse(texto)
        except (SyntaxError, tokenize.TokenError, ValueError):
            # Sem conseguir separar comentário de código, vale o texto inteiro.
            return [(0, len(texto))]
    for no in ast.walk(arvore):
        if isinstance(no, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            primeiro = no.body[0] if no.body else None
            if (isinstance(primeiro, ast.Expr) and isinstance(primeiro.value, ast.Constant)
                    and isinstance(primeiro.value.value, str)):
                fim = inicios[primeiro.end_lineno] if primeiro.end_lineno < len(inicios) else len(texto)
                trechos.append((inicios[primeiro.lineno - 1], fim))
    return trechos


def trechos_de_comentario(caminho: str, texto: str) -> list[tuple[int, int]]:
    """Os intervalos do texto que são comentário, conforme o tipo de arquivo."""
    sufixo = PurePosixPath(caminho).suffix.lower()
    if sufixo == ".py":
        return _trechos_py(texto)
    if sufixo in (".cmd", ".bat"):
        expressoes = (_COMENTARIO_CMD,)
    elif sufixo in (".js", ".mjs", ".css"):
        expressoes = (_COMENTARIO_BLOCO, _COMENTARIO_BARRAS)
    elif sufixo in (".html", ".htm"):
        expressoes = (_COMENTARIO_HTML,)
    elif sufixo in _CERQUILHA:
        expressoes = (_COMENTARIO_CERQUILHA,)
    else:
        return []
    return [m.span() for e in expressoes for m in e.finditer(texto)]


def _regras() -> tuple:
    """As REGRAS e o perfil da conta que publica.

    O perfil vale só para essa conta: o vazamento é o caminho desta máquina, e
    um perfil inventado de exemplo nos testes (`C:\\Users\\x`) não é vazamento.
    """
    conta = Path.home().name
    if not conta:
        return REGRAS
    perfil = re.compile(r"\b[A-Za-z]:[\\/]+Users[\\/]+" + re.escape(conta) + r"(?![\w.-])", re.I)
    return REGRAS + (("caminho de perfil de usuario", perfil),)


def _achados_no_arquivo(rel: str, dados: bytes, regras: tuple) -> list[str]:
    if PurePosixPath(rel).suffix.lower() in EXTENSOES_PROIBIDAS:
        return [f"{rel}: extensao proibida ({PurePosixPath(rel).suffix.lower()})"]
    texto = dados.decode("utf-8", errors="ignore")
    achados = []
    for i, linha in enumerate(texto.splitlines(), 1):
        for nome, expressao in regras:
            if expressao.search(linha):
                achados.append(f"{rel}:{i}: {nome}")
    trechos = trechos_de_comentario(rel, texto)
    for m in DATA.finditer(texto):
        if any(inicio <= m.start() < fim for inicio, fim in trechos):
            achados.append(f"{rel}:{texto.count(chr(10), 0, m.start()) + 1}: comentario com data")
    return list(dict.fromkeys(achados))


def varrer(copia: Path) -> list[str]:
    """Achados na cópia, sem o valor achado. Lista vazia: limpa."""
    itens = [(p.relative_to(copia).as_posix(), p.read_bytes()) for p in sorted(copia.rglob("*"))
             if p.is_file() and ".git" not in p.relative_to(copia).parts]
    de_paciente = checar_paciente.varrer(itens)
    achados = [f"dado de paciente: {linha}" for linha in checar_paciente.laudo(de_paciente)]
    marcados = {caminho for caminho, _ in de_paciente}
    regras = _regras()
    for rel, dados in itens:
        if rel not in marcados:
            achados += _achados_no_arquivo(rel, dados, regras)
    presentes = {rel for rel, _ in itens}
    achados += [f"piso do repositorio publico: falta {nome}" for nome in PISO if nome not in presentes]
    return achados


# ---------------------------------------------------------------- selo da varredura

def impressao(copia: Path) -> str:
    """SHA-256 dos caminhos e do conteúdo de cada arquivo da cópia, fora o `.git` e o selo."""
    soma = hashlib.sha256()
    arquivos = sorted((p.relative_to(copia).as_posix(), p) for p in copia.rglob("*")
                      if p.is_file() and ".git" not in p.relative_to(copia).parts)
    for rel, arquivo in arquivos:
        if rel != MARCA:
            soma.update(f"{rel}\0{hashlib.sha256(arquivo.read_bytes()).hexdigest()}\n".encode("utf-8"))
    return soma.hexdigest()


def selar(copia: Path) -> None:
    (copia / MARCA).write_bytes(impressao(copia).encode("ascii") + b"\n")


def varredura_confere(copia: Path) -> bool:
    """O selo existe e bate com a cópia como ela está agora."""
    try:
        return (copia / MARCA).read_bytes().decode("ascii").strip() == impressao(copia)
    except (OSError, UnicodeError):
        return False


def exigir_varredura(copia: Path) -> None:
    if not varredura_confere(copia):
        raise PublicacaoRecusada(f"{copia} nao tem uma varredura valida (sem selo, ou mexida depois "
                                 "dele): rode packager/publicar.py de novo")


# ---------------------------------------------------------------- montagem

def _somente_leitura(funcao, caminho, _erro):
    # O git grava objeto como somente leitura, e o rmtree para no primeiro.
    os.chmod(caminho, 0o666)
    funcao(caminho)


def _esvaziar(destino: Path) -> None:
    """Apaga tudo ao lado do `.git`, que é o histórico publicado e nunca sai do lugar."""
    if not destino.is_dir():
        return
    for item in destino.iterdir():
        if item.name == ".git":
            continue
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item, onexc=_somente_leitura)
        else:
            os.chmod(item, 0o666)
            item.unlink()


def montar(oficina: Path = RAIZ, destino: Path | None = None, *, entradas=None,
           ler=arquivos_do_commit, em_nuvem=None, ambiente=os.environ, dizer=print) -> int:
    """Monta, varre e sela a cópia. Devolve quantos arquivos foram copiados."""
    destino = destino_padrao(ambiente) if destino is None else destino
    problemas = problemas_de_destino(destino, oficina, ambiente, em_nuvem)
    if problemas:
        raise PublicacaoRecusada("destino proibido:\n  " + "\n  ".join(problemas))
    (destino / MARCA).unlink(missing_ok=True)
    entradas = ler_permitidos() if entradas is None else entradas
    versionados = ler(oficina)
    copias = selecionar(versionados, entradas)
    dizer(f"montando {len(copias)} arquivos do ultimo commit da oficina em {destino}")
    try:
        destino.mkdir(parents=True, exist_ok=True)
        _esvaziar(destino)
        for rel, origem in sorted(copias.items()):
            alvo = destino / rel
            alvo.parent.mkdir(parents=True, exist_ok=True)
            alvo.write_bytes(versionados[origem])
        achados = varrer(destino)
        if achados:
            raise PublicacaoRecusada(f"{len(achados)} achado(s) na copia; ela foi esvaziada e nada "
                                     "foi publicado:\n  " + "\n  ".join(achados))
        selar(destino)
    except BaseException:
        # BaseException de propósito: com Ctrl+C no meio, a cópia parcial também
        # não pode ficar com cara de pronta. Sem selo ela já não serve de fonte;
        # esvaziar é a segunda trava.
        try:
            _esvaziar(destino)
        except OSError:
            pass
        raise
    return len(copias)


# ---------------------------------------------------------------- commit

def _git(destino: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(destino), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _selo_fora_do_git(destino: Path) -> None:
    exclude = destino / ".git" / "info" / "exclude"
    linha = "/" + MARCA
    atual = exclude.read_text(encoding="utf-8-sig") if exclude.is_file() else ""
    if linha not in atual.splitlines():
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with open(exclude, "a", encoding="utf-8", newline="\n") as f:
            f.write(("\n" if atual and not atual.endswith("\n") else "") + linha + "\n")


def commit(destino: Path) -> str:
    """Um commit por versão na cópia. Devolve "feito" ou "sem_mudanca". Nunca envia."""
    exigir_varredura(destino)
    with open(destino / "pyproject.toml", "rb") as f:
        versao = tomllib.load(f)["project"]["version"]
    mensagem = f"Scan-to-Text {versao}"
    if not (destino / ".git").is_dir():
        r = _git(destino, "init", "-q", "-b", "main")
        if r.returncode != 0:
            raise PublicacaoRecusada(f"git init falhou: {r.stderr.strip()}")
    _selo_fora_do_git(destino)
    r = _git(destino, "add", "-A")
    if r.returncode != 0:
        raise PublicacaoRecusada(f"git add falhou: {r.stderr.strip()}")
    if _git(destino, "diff", "--cached", "--quiet").returncode == 0:
        return "sem_mudanca"
    ultimo = _git(destino, "log", "-1", "--format=%s")
    if ultimo.returncode == 0 and ultimo.stdout.strip() == mensagem:
        raise PublicacaoRecusada(f"a versao {versao} ja tem commit na copia com outro conteudo: "
                                 "suba a versao no pyproject.toml")
    r = _git(destino, "commit", "-q", "-m", mensagem)
    if r.returncode != 0:
        raise PublicacaoRecusada(f"git commit falhou: {r.stderr.strip()}")
    return "feito"


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    destino = destino_padrao()
    try:
        quantos = montar(RAIZ, destino)
        print(f"copia publica: {quantos} arquivos, varredura limpa e selada")
        if "--commit" in argv:
            print("commit: " + {"feito": "feito", "sem_mudanca": "nada mudou"}[commit(destino)])
        else:
            print("sem --commit: copia montada e conferida, nada commitado")
    except PublicacaoRecusada as erro:
        print(f"RECUSADO: {erro}")
        return 2
    print(f"PARADO ANTES DO ENVIO, de proposito: {destino}")
    print("  o envio ao GitHub e um passo a parte, fora deste script.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
