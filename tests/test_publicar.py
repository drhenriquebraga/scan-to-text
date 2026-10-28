import ast
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "packager"))

import montar_pacote as mp  # noqa: E402
import publicar as pub  # noqa: E402

# Os segredos e caminhos plantados nascem de partes separadas: escritos inteiros,
# este arquivo, que também é publicado, reprovaria a própria varredura.
TOKEN = "ghp_" + "Ab1" * 12
CHAVE = "-----BEGIN " + "PRIVATE KEY-----"
OFICINA = "C:" + "\\AI_PROJETOS\\SCAN_TO_TEXT"
DATA = "28/" + "09/2026"

BASE = {
    "app/scan_to_text/__init__.py": '"""Pacote do motor."""\n',
    "app/scan_to_text/motor.py": 'EXEMPLO = "02/04/2026"  # data de exemplo fica em string\n',
    "app/packs/pt-BR/textos.yaml": "pasta: DESCONHECIDO  # nome da pasta\n",
    "tests/test_motor.py": "def test_motor():\n    assert True\n",
    "packager/INSTALL.cmd": "@echo off\r\nREM instala\r\npause\r\nexit /b 0\r\n",
    "pyproject.toml": '[project]\nname = "scan-to-text"\nversion = "1.2.3"\n',
    ".gitattributes": "*.cmd text eol=crlf\n",
    ".gitignore": "__pycache__/\n",
    "publico/README.md": "# Scan-to-Text\n\nNot a medical device.\n",
    "publico/LICENSE": "Apache License 2.0\n",
    "publico/NOTICE": "Scan-to-Text\n",
    "README.md": "# Scan-to-Text (oficina)\n",
    "CLAUDE.md": "# Regras da oficina\n",
    "docs/superpowers/specs/spec.md": "# Spec\n",
}
ENTRADAS = ["app/scan_to_text/", "app/packs/", "tests/", "packager/INSTALL.cmd", "pyproject.toml",
            ".gitattributes", ".gitignore"]
PUBLICADOS = {"app/scan_to_text/__init__.py", "app/scan_to_text/motor.py",
              "app/packs/pt-BR/textos.yaml", "tests/test_motor.py", "packager/INSTALL.cmd",
              "pyproject.toml", ".gitattributes", ".gitignore", "README.md", "LICENSE", "NOTICE"}


def _escrever(raiz: Path, arquivos: dict[str, str | bytes]) -> Path:
    for nome, dados in arquivos.items():
        (raiz / nome).parent.mkdir(parents=True, exist_ok=True)
        if isinstance(dados, str):
            dados = dados.encode("utf-8")
        (raiz / nome).write_bytes(dados)
    return raiz


def _ler_disco(oficina: Path) -> dict[str, bytes]:
    return {p.relative_to(oficina).as_posix(): p.read_bytes()
            for p in sorted(oficina.rglob("*")) if p.is_file()}


def _ambiente(tmp_path: Path) -> dict:
    return {"LOCALAPPDATA": str(tmp_path / "local"), "SystemDrive": str(tmp_path / "sistema")}


def _git_de_teste(monkeypatch) -> None:
    if not shutil.which("git"):
        pytest.skip("sem git")
    for chave, valor in (("GIT_AUTHOR_NAME", "Teste"), ("GIT_AUTHOR_EMAIL", "teste@exemplo.invalid"),
                         ("GIT_COMMITTER_NAME", "Teste"),
                         ("GIT_COMMITTER_EMAIL", "teste@exemplo.invalid")):
        monkeypatch.setenv(chave, valor)


def _montar(tmp_path: Path, extra: dict | None = None, sem: tuple = (), **opcoes):
    oficina = _escrever(tmp_path / "oficina", {k: v for k, v in {**BASE, **(extra or {})}.items()
                                               if k not in sem})
    destino = tmp_path / "local" / "RADIOTERAPIA_AI_PUBLICACAO" / "scan-to-text"
    kw = dict(entradas=ENTRADAS, ler=_ler_disco, em_nuvem=lambda _: False,
              ambiente=_ambiente(tmp_path), dizer=lambda *_: None)
    kw.update(opcoes)
    pub.montar(oficina, destino, **kw)
    return oficina, destino


def _copiados(destino: Path) -> set[str]:
    """O que a cópia publica: fora o `.git` e o selo da varredura."""
    return {p.relative_to(destino).as_posix() for p in destino.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(destino).parts} - {pub.MARCA}


def _recusa(tmp_path: Path, extra: dict | None = None, **kw) -> str:
    with pytest.raises(pub.PublicacaoRecusada) as erro:
        _montar(tmp_path, extra, **kw)
    return str(erro.value)


# ---------------------------------------------------------------- lista e cópia

def test_copia_so_o_que_a_lista_permite_e_publico_vai_para_a_raiz(tmp_path):
    _, destino = _montar(tmp_path)
    assert _copiados(destino) == PUBLICADOS
    assert (destino / "README.md").read_text(encoding="utf-8").startswith("# Scan-to-Text\n\nNot")
    assert (destino / "packager" / "INSTALL.cmd").read_bytes().count(b"\r\n") == 4


def test_so_o_conteudo_do_ultimo_commit_entra(tmp_path, monkeypatch):
    _git_de_teste(monkeypatch)
    oficina = _escrever(tmp_path / "oficina", BASE)
    for comando in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
        subprocess.run(["git", *comando], cwd=oficina, check=True, capture_output=True)
    # Depois do commit: mudança só no disco, arquivo novo só no índice, arquivo
    # fora do Git e arquivo apagado do disco. Vale o commit, para todos.
    _escrever(oficina, {"app/scan_to_text/motor.py": "X = 'nunca commitado'\n",
                        "app/scan_to_text/novo.py": "Y = 'so no indice'\n",
                        "app/scan_to_text/rascunho.py": "Z = 1\n"})
    subprocess.run(["git", "add", "app/scan_to_text/novo.py"], cwd=oficina, check=True)
    (oficina / "tests" / "test_motor.py").unlink()
    destino = tmp_path / "local" / "pub"
    pub.montar(oficina, destino, entradas=ENTRADAS, em_nuvem=lambda _: False,
               ambiente=_ambiente(tmp_path), dizer=lambda *_: None)
    assert _copiados(destino) == PUBLICADOS
    for rel in ("app/scan_to_text/motor.py", "tests/test_motor.py"):
        # O fim de linha segue os atributos e o core.autocrlf, como num checkout.
        assert (destino / rel).read_bytes().replace(b"\r\n", b"\n") == BASE[rel].encode(), rel
    assert (destino / "packager" / "INSTALL.cmd").read_bytes() == BASE["packager/INSTALL.cmd"].encode()


def test_oficina_sem_commit_recusa(tmp_path):
    if not shutil.which("git"):
        pytest.skip("sem git")
    oficina = _escrever(tmp_path / "oficina", BASE)
    subprocess.run(["git", "init", "-q"], cwd=oficina, check=True)
    with pytest.raises(pub.PublicacaoRecusada, match="commit"):
        pub.arquivos_do_commit(oficina)


def test_nova_montagem_preserva_o_git_e_tira_o_que_saiu_da_lista(tmp_path):
    destino = tmp_path / "local" / "RADIOTERAPIA_AI_PUBLICACAO" / "scan-to-text"
    _escrever(destino, {".git/HEAD": "ref: refs/heads/main\n", "velho.py": "X = 1\n"})
    _montar(tmp_path)
    assert (destino / ".git" / "HEAD").read_text(encoding="utf-8") == "ref: refs/heads/main\n"
    assert not (destino / "velho.py").exists()


def test_ler_permitidos_ignora_comentario_e_recusa_caminho_que_escapa(tmp_path):
    lista = tmp_path / "permitidos.txt"
    lista.write_text("# comentario\n\napp/scan_to_text/\r\npyproject.toml  \n", encoding="utf-8-sig")
    assert pub.ler_permitidos(lista) == ["app/scan_to_text/", "pyproject.toml"]
    for ruim in ("../fora.py", "C:/x.py", "/x.py", "app/../CLAUDE.md"):
        lista.write_text(ruim + "\n", encoding="utf-8")
        with pytest.raises(pub.PublicacaoRecusada):
            pub.ler_permitidos(lista)


def test_lista_real_cobre_o_montador_e_deixa_a_oficina_de_fora():
    entradas = pub.ler_permitidos()
    for caminho in ("app/scan_to_text/__main__.py", "app/scan_to_text/instalar.py",
                    "app/packs/pt-BR/textos.yaml", "packager/INSTALL.cmd", "packager/LEIA-ME.txt",
                    "packager/montar_pacote.py", "packager/catalogo.json", "pyproject.toml",
                    "requirements.txt", "tests/test_montar.py", "tools/checar_licencas.py",
                    "tools/montar_kit.py", "diagnostico/TESTAR.cmd", ".gitattributes", ".gitignore"):
        assert pub.permitido(caminho, entradas), caminho
    for caminho in ("CLAUDE.md", "AGENTS.md", "CONTEXT.md", "PRODUCT.md", "README.md",
                    "docs/superpowers/specs/2026-09-27-scan-to-text-design.md", "docs/adr/0001.md",
                    "_montagem/terceiros/x.whl", ".venv/pyvenv.cfg", ".claude/settings.json",
                    "publico/README.md"):
        assert not pub.permitido(caminho, entradas), caminho


PASTAS_DE_MODULO = ("app", "tools", "tests", "packager")


def _importados(arquivo: Path) -> set[str]:
    nomes = set()
    for no in ast.walk(ast.parse(arquivo.read_text(encoding="utf-8-sig"))):
        if isinstance(no, ast.Import):
            nomes.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and not no.level and no.module:
            nomes.add(no.module.split(".")[0])
    return nomes


def _modulo_local(nome: str) -> str | None:
    for pasta in PASTAS_DE_MODULO:
        for candidato in (f"{pasta}/{nome}.py", f"{pasta}/{nome}/__init__.py"):
            if (RAIZ / candidato).is_file():
                return candidato
    return None


def test_todo_modulo_local_que_um_arquivo_publicado_importa_tambem_e_publicado():
    # Sem isso a suíte da cópia pública quebra na coleta (ModuleNotFoundError).
    entradas = pub.ler_permitidos()
    faltam = set()
    for pasta in PASTAS_DE_MODULO:
        for arquivo in sorted((RAIZ / pasta).rglob("*.py")):
            rel = arquivo.relative_to(RAIZ).as_posix()
            if "__pycache__" in arquivo.parts or not pub.permitido(rel, entradas):
                continue
            for nome in _importados(arquivo):
                local = _modulo_local(nome)
                if local and not pub.permitido(local, entradas):
                    faltam.add(f"{rel} importa {local}")
    assert not faltam, sorted(faltam)


# ---------------------------------------------------------------- destino

def test_recusa_destino_proibido(tmp_path):
    amb = _ambiente(tmp_path)
    oficina = tmp_path / "oficina"
    sincronizada = tmp_path / "drive"
    nuvem = lambda p: str(p).startswith(str(sincronizada))  # noqa: E731
    for proibido in (oficina / "publicacao", oficina,
                     Path(amb["SystemDrive"] + "\\") / "RADIOTERAPIA_AI" / "pub",
                     tmp_path / "local" / "Scan-to-Text" / "pub",
                     tmp_path / "local" / "RADIOTERAPIA_AI" / "pub",
                     sincronizada / "scan-to-text"):
        assert pub.problemas_de_destino(proibido, oficina, amb, nuvem), proibido
    livre = tmp_path / "local" / "RADIOTERAPIA_AI_PUBLICACAO" / "scan-to-text"
    assert pub.problemas_de_destino(livre, oficina, amb, nuvem) == []


def test_destino_proibido_nao_recebe_nada(tmp_path):
    oficina = _escrever(tmp_path / "oficina", BASE)
    destino = oficina / "publicacao"
    with pytest.raises(pub.PublicacaoRecusada, match="oficina"):
        pub.montar(oficina, destino, entradas=ENTRADAS, ler=_ler_disco,
                   em_nuvem=lambda _: False, ambiente=_ambiente(tmp_path), dizer=lambda *_: None)
    assert not destino.exists()


def test_destino_padrao_pela_variavel_ou_pelo_localappdata(tmp_path):
    assert pub.destino_padrao({"RADIOTERAPIA_AI_PUBLICACAO": str(tmp_path)}) == tmp_path / "scan-to-text"
    assert pub.destino_padrao({"LOCALAPPDATA": str(tmp_path)}) == \
        tmp_path / "RADIOTERAPIA_AI_PUBLICACAO" / "scan-to-text"


# ---------------------------------------------------------------- varredura na cópia

def test_segredo_recusa_sem_imprimir_o_valor_e_a_copia_some(tmp_path):
    motor = BASE["app/scan_to_text/motor.py"] + f'CHAVE = "{TOKEN}"\n'
    mensagem = _recusa(tmp_path, {"app/scan_to_text/motor.py": motor,
                                  "tests/test_motor.py": f'"""{CHAVE}"""\n'})
    assert "app/scan_to_text/motor.py:2" in mensagem and "token" in mensagem
    assert "tests/test_motor.py:1" in mensagem and "chave privada" in mensagem
    assert TOKEN not in mensagem and "BEGIN" not in mensagem
    destino = tmp_path / "local" / "RADIOTERAPIA_AI_PUBLICACAO" / "scan-to-text"
    assert _copiados(destino) == set()


def test_caminho_da_oficina_e_do_perfil_de_quem_publica_recusam(tmp_path):
    conta = Path.home().name
    mensagem = _recusa(tmp_path, {
        "app/scan_to_text/motor.py": f'"""Lido de {OFICINA}\\app."""\n',
        "tests/test_motor.py": (f'BASE = r"C:\\Users\\{conta}\\Desktop"\n'
                                'EXEMPLO = r"C:\\Users\\x\\Meu Drive"\n')})
    assert "app/scan_to_text/motor.py:1" in mensagem and "oficina" in mensagem
    assert "tests/test_motor.py:1" in mensagem and "perfil" in mensagem
    # Perfil inventado de exemplo não é vazamento: só o da conta que publica.
    assert "tests/test_motor.py:2" not in mensagem
    assert conta not in mensagem


def test_dado_de_paciente_recusa_sem_imprimir_o_caminho(tmp_path):
    mensagem = _recusa(tmp_path, {
        "tests/FULANA DE TESTE - 2026-10-25/COMPLETO.md": "# FULANA DE TESTE\n",
        "tests/laudo.pdf": b"%PDF-1.7 falso"})
    assert "FULANA" not in mensagem and "laudo.pdf" not in mensagem
    assert mensagem.count("[completo]") == 1 and mensagem.count("[documento]") == 1


def test_extensao_proibida_recusa(tmp_path):
    mensagem = _recusa(tmp_path, {"tests/certificado.pfx": b"\x30\x82"})
    assert "tests/certificado.pfx" in mensagem and ".pfx" in mensagem


def test_comentario_com_data_recusa_e_string_com_data_passa(tmp_path):
    mensagem = _recusa(tmp_path, {
        "app/scan_to_text/motor.py": (f'"""Motor.\n\nDecidido em {DATA}."""\n'
                                      f'X = "{DATA}"\n'
                                      f'Y = 1  # mudou em {DATA}\n'),
        "packager/INSTALL.cmd": (f"@echo off\r\nREM criado em {DATA}\r\necho {DATA}\r\n"
                                 f"echo x & REM {DATA}\r\necho y && :: {DATA}\r\n"),
        "app/packs/pt-BR/textos.yaml": f"pasta: DESCONHECIDO\n# revisto em {DATA}\nx: '{DATA}'\n",
        "tests/test_motor.py": f"def test_motor():\n    pass\n\n\n# {DATA}\n"})
    for linha in ("app/scan_to_text/motor.py:3", "app/scan_to_text/motor.py:5",
                  "packager/INSTALL.cmd:2", "packager/INSTALL.cmd:4", "packager/INSTALL.cmd:5",
                  "app/packs/pt-BR/textos.yaml:2", "tests/test_motor.py:5"):
        assert f"{linha}: comentario com data" in mensagem, linha
    for linha in ("app/scan_to_text/motor.py:4", "packager/INSTALL.cmd:3",
                  "app/packs/pt-BR/textos.yaml:3"):
        assert f"{linha}:" not in mensagem, linha


def test_comentario_js_css_html_com_data_recusa(tmp_path):
    mensagem = _recusa(tmp_path, {
        "app/scan_to_text/tela/web/app.js": f'const x = 1; // {DATA}\nconst y = "{DATA}";\n',
        "app/scan_to_text/tela/web/estilo.css": f"/* revisto\n   em {DATA} */\nbody {{}}\n",
        "app/scan_to_text/tela/web/index.html": f"<p>{DATA}</p>\n<!-- {DATA} -->\n"})
    assert "app/scan_to_text/tela/web/app.js:1: comentario com data" in mensagem
    assert "app.js:2:" not in mensagem
    assert "app/scan_to_text/tela/web/estilo.css:2: comentario com data" in mensagem
    assert "app/scan_to_text/tela/web/index.html:2: comentario com data" in mensagem
    assert "index.html:1:" not in mensagem


def test_piso_do_repositorio_publico_e_obrigatorio(tmp_path):
    mensagem = _recusa(tmp_path, sem=("publico/LICENSE", ".gitignore"))
    assert "LICENSE" in mensagem and ".gitignore" in mensagem


# ---------------------------------------------------------------- selo da varredura

def _montador_recusa(fonte: Path) -> None:
    with pytest.raises(mp.MontagemRecusada, match="publicar.py"):
        mp.exigir_copia_publica(fonte)


def test_copia_varrida_e_selada_serve_de_fonte_ao_montador(tmp_path):
    _, destino = _montar(tmp_path)
    assert (destino / pub.MARCA).is_file() and pub.varredura_confere(destino)
    mp.exigir_copia_publica(destino)


@pytest.mark.parametrize("onde", ["copia", "varredura"])
@pytest.mark.parametrize("erro", [PermissionError(13, "arquivo preso"), KeyboardInterrupt()],
                         ids=["arquivo_preso", "ctrl_c"])
def test_montagem_interrompida_esvazia_a_copia_e_o_montador_recusa(tmp_path, monkeypatch, onde, erro):
    _, destino = _montar(tmp_path)
    assert pub.varredura_confere(destino)
    if onde == "varredura":
        def parar(_copia):
            raise erro
        monkeypatch.setattr(pub, "varrer", parar)
    else:
        gravar = Path.write_bytes

        def parar(self, dados):
            # Para no último arquivo, depois de o pyproject.toml e o motor já estarem na cópia.
            if destino in self.parents and self.name == "test_motor.py":
                raise erro
            return gravar(self, dados)
        monkeypatch.setattr(Path, "write_bytes", parar)
    with pytest.raises(type(erro)):
        _montar(tmp_path, {"app/scan_to_text/motor.py": "X = 2\n"})
    monkeypatch.undo()
    assert _copiados(destino) == set() and not (destino / pub.MARCA).exists()
    _montador_recusa(destino)


def test_copia_mexida_depois_da_varredura_nao_serve_de_fonte(tmp_path):
    _, destino = _montar(tmp_path)
    (destino / "app" / "scan_to_text" / "motor.py").write_text(f"# decidido em {DATA}\n",
                                                               encoding="utf-8")
    _montador_recusa(destino)
    _, destino = _montar(tmp_path)
    (destino / "app" / "scan_to_text" / "extra.py").write_text("X = 1\n", encoding="utf-8")
    _montador_recusa(destino)
    _, destino = _montar(tmp_path)
    (destino / "tests" / "test_motor.py").unlink()
    _montador_recusa(destino)


def test_pasta_qualquer_com_pyproject_nao_serve_de_fonte(tmp_path):
    # Um clone da oficina fora dela tem pyproject.toml, mas não passou pela varredura.
    clone = _escrever(tmp_path / "clone", BASE)
    _montador_recusa(clone)
    (clone / pub.MARCA).write_text("0" * 64 + "\n", encoding="ascii")
    _montador_recusa(clone)


# ---------------------------------------------------------------- commit

def _git(pasta: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(pasta), *args], capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout.strip()


def test_commit_recusa_copia_sem_varredura_valida(tmp_path, monkeypatch):
    _git_de_teste(monkeypatch)
    _, destino = _montar(tmp_path)
    (destino / "app" / "scan_to_text" / "motor.py").write_text(f"# decidido em {DATA}\n",
                                                               encoding="utf-8")
    with pytest.raises(pub.PublicacaoRecusada, match="varredura"):
        pub.commit(destino)
    assert not (destino / ".git").exists()


def test_um_commit_por_versao_e_nenhum_envio(tmp_path, monkeypatch):
    _git_de_teste(monkeypatch)
    oficina, destino = _montar(tmp_path)
    assert pub.commit(destino) == "feito"
    assert _git(destino, "log", "--format=%s") == "Scan-to-Text 1.2.3"
    assert _git(destino, "remote") == ""
    assert _git(destino, "status", "--porcelain") == ""
    # O selo é da máquina que publica: fica fora do repositório público.
    assert pub.MARCA not in _git(destino, "ls-files").splitlines()
    assert pub.varredura_confere(destino)
    _montar(tmp_path)
    assert pub.commit(destino) == "sem_mudanca"
    _montar(tmp_path, {"app/scan_to_text/motor.py": "X = 2\n"})
    with pytest.raises(pub.PublicacaoRecusada, match="suba a versao"):
        pub.commit(destino)
    _montar(tmp_path, {"app/scan_to_text/motor.py": "X = 2\n",
                       "pyproject.toml": '[project]\nname = "scan-to-text"\nversion = "1.2.4"\n'})
    assert pub.commit(destino) == "feito"
    assert _git(destino, "log", "--format=%s").splitlines() == ["Scan-to-Text 1.2.4",
                                                                 "Scan-to-Text 1.2.3"]
