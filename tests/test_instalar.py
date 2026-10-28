import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

from scan_to_text import instalar as inst
from test_montar import _cmd_de_ambiente, _saidas_pausadas

RAIZ = Path(__file__).resolve().parents[1]
HOJE = date(2026, 10, 25)


def _pacote(pasta: Path, versao: str = "1.2.3", extra: dict[str, bytes] | None = None) -> Path:
    arquivos = {
        "python/python.exe": b"MZ python", "python/pythonw.exe": b"MZ pythonw",
        "app/scan_to_text/__init__.py": f"VERSAO = {versao!r}\n".encode(),
        "webview2/msedgewebview2.exe": b"MZ webview2", "LEIA-ME.txt": b"leia\r\n",
        "entrega.txt": f"PROJETO=SCAN_TO_TEXT\r\nVERSAO={versao}\r\n".encode("ascii"),
        **(extra or {}),
    }
    for nome, dados in arquivos.items():
        (pasta / nome).parent.mkdir(parents=True, exist_ok=True)
        (pasta / nome).write_bytes(dados)
    return pasta


class _Chamadas:
    def __init__(self, codigo_icacls: int = 0):
        self.comandos, self.abertos, self.ditos = [], [], []
        self.codigo_icacls = codigo_icacls

    def executar(self, comando, **_):
        self.comandos.append(comando)
        return subprocess.CompletedProcess(comando, self.codigo_icacls, b"", b"")

    def abrir(self, destino):
        self.abertos.append(destino)

    def dizer(self, texto=""):
        self.ditos.append(texto)


def _instalar(tmp_path: Path, pacote: Path, chamadas: _Chamadas | None = None, **extra) -> Path:
    chamadas = chamadas or _Chamadas()
    opcoes = dict(executar=chamadas.executar, abrir=chamadas.abrir, dizer=chamadas.dizer,
                  espaco_livre=lambda _: 10 ** 12)
    opcoes.update(extra)
    return inst.instalar(pacote, tmp_path / "RADIOTERAPIA_AI",
                         [tmp_path / "desktop", tmp_path / "menu"], HOJE, **opcoes)


def _registro(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "RADIOTERAPIA_AI" / "_instalados.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _sem_espera(monkeypatch):
    monkeypatch.setattr(inst, "ESPERA_S", 0)


# ---------------------------------------------------------------- instalação

def test_instala_copia_atalhos_icacls_registro_e_abre(tmp_path):
    pacote = _pacote(tmp_path / "extraido" / "Scan-to-Text")
    chamadas = _Chamadas()
    destino = _instalar(tmp_path, pacote, chamadas)
    assert destino == tmp_path / "RADIOTERAPIA_AI" / "SCAN_TO_TEXT"
    assert (destino / "python" / "pythonw.exe").read_bytes() == b"MZ pythonw"
    assert (destino / "entrega.txt").is_file() and (destino / "LEIA-ME.txt").is_file()
    for pasta in (tmp_path / "desktop", tmp_path / "menu"):
        atalho = (pasta / "Scan-to-Text.lnk").read_bytes()
        assert "-m scan_to_text".encode("utf-16-le") in atalho
    icacls = [c for c in chamadas.comandos if Path(c[0]).name.lower() == "icacls.exe"]
    assert len(icacls) == 1 and icacls[0][1] == str(destino / "webview2")
    assert "*S-1-15-2-1:(OI)(CI)(RX)" in icacls[0] and "*S-1-15-2-2:(OI)(CI)(RX)" in icacls[0]
    assert _registro(tmp_path) == {"SCAN_TO_TEXT": {"versao": "1.2.3", "data": "2026-10-25",
                                                    "caminho": str(destino)}}
    assert chamadas.abertos == [destino]
    assert sorted(p.name for p in (tmp_path / "RADIOTERAPIA_AI").iterdir()) == [
        "SCAN_TO_TEXT", "_instalados.json"]


def test_mescla_instalados_sem_apagar_chave_alheia(tmp_path):
    raiz = tmp_path / "RADIOTERAPIA_AI"
    raiz.mkdir()
    alheia = {"versao": "8.5", "data": "2026-10-14", "caminho": "X:\\OUTRO"}
    # Com BOM, como grava o Bloco de Notas do LTSB.
    (raiz / "_instalados.json").write_text(
        json.dumps({"AUTO_CONTORNO": alheia, "SCAN_TO_TEXT": {"versao": "0.9"}}),
        encoding="utf-8-sig")
    _instalar(tmp_path, _pacote(tmp_path / "pacote"))
    registro = _registro(tmp_path)
    assert registro["AUTO_CONTORNO"] == alheia
    assert registro["SCAN_TO_TEXT"]["versao"] == "1.2.3"
    assert list(registro) == ["AUTO_CONTORNO", "SCAN_TO_TEXT"]


def test_registro_ilegivel_nao_e_sobrescrito(tmp_path):
    raiz = tmp_path / "RADIOTERAPIA_AI"
    raiz.mkdir()
    (raiz / "_instalados.json").write_bytes(b'{"AUTO_CONTORNO": {"versao": "8.5"')
    chamadas = _Chamadas()
    destino = _instalar(tmp_path, _pacote(tmp_path / "pacote"), chamadas)
    assert (raiz / "_instalados.json").read_bytes() == b'{"AUTO_CONTORNO": {"versao": "8.5"'
    assert (destino / "python" / "pythonw.exe").is_file()
    assert any("registro" in d.lower() and "aviso" in d.lower() for d in chamadas.ditos)


def test_registro_ocupado_por_trava_viva_so_avisa(tmp_path):
    raiz = tmp_path / "RADIOTERAPIA_AI"
    raiz.mkdir()
    (raiz / "_instalados.json.trava").write_bytes(b"")
    chamadas = _Chamadas()
    _instalar(tmp_path, _pacote(tmp_path / "pacote"), chamadas)
    assert not (raiz / "_instalados.json").exists()
    assert any("registro" in d.lower() and "aviso" in d.lower() for d in chamadas.ditos)


def test_trava_velha_do_registro_e_vencida(tmp_path):
    import os
    raiz = tmp_path / "RADIOTERAPIA_AI"
    raiz.mkdir()
    trava = raiz / "_instalados.json.trava"
    trava.write_bytes(b"")
    os.utime(trava, (1, 1))
    _instalar(tmp_path, _pacote(tmp_path / "pacote"))
    assert _registro(tmp_path)["SCAN_TO_TEXT"]["versao"] == "1.2.3"
    assert not trava.exists()


def test_atualizacao_preserva_config_json_e_troca_o_resto(tmp_path):
    destino = _instalar(tmp_path, _pacote(tmp_path / "v1", "1.0.0",
                                          {"app/scan_to_text/velho.py": b"X = 1\n"}))
    config = '{"origens": ["\\\\\\\\servidor\\\\scanner"], "destinos": ["D:\\\\PACIENTES"]}'
    (destino / "config.json").write_text(config, encoding="utf-8-sig")
    antes = (destino / "config.json").read_bytes()
    _instalar(tmp_path, _pacote(tmp_path / "v2", "2.0.0"))
    assert (destino / "config.json").read_bytes() == antes
    assert not (destino / "app" / "scan_to_text" / "velho.py").exists()
    assert (destino / "entrega.txt").read_bytes().endswith(b"VERSAO=2.0.0\r\n")
    assert _registro(tmp_path)["SCAN_TO_TEXT"]["versao"] == "2.0.0"
    assert sorted(p.name for p in (tmp_path / "RADIOTERAPIA_AI").iterdir()) == [
        "SCAN_TO_TEXT", "_instalados.json"]


def test_app_aberto_recusa_a_troca_e_nada_muda(tmp_path):
    destino = _instalar(tmp_path, _pacote(tmp_path / "v1", "1.0.0"))
    (destino / "config.json").write_text("{}", encoding="utf-8")
    with open(destino / "python" / "pythonw.exe", "rb"):
        with pytest.raises(inst.InstalacaoRecusada, match="aberto"):
            _instalar(tmp_path, _pacote(tmp_path / "v2", "2.0.0"))
    assert (destino / "entrega.txt").read_bytes().endswith(b"VERSAO=1.0.0\r\n")
    assert (destino / "config.json").read_text(encoding="utf-8") == "{}"
    assert sorted(p.name for p in (tmp_path / "RADIOTERAPIA_AI").iterdir()) == [
        "SCAN_TO_TEXT", "_instalados.json"]


def test_retoma_troca_interrompida_sem_perder_config(tmp_path):
    destino = _instalar(tmp_path, _pacote(tmp_path / "v1", "1.0.0"))
    (destino / "config.json").write_text('{"ping_min": 5}', encoding="utf-8")
    # Queda de energia entre as duas renomeações: só sobrou a instalação antiga.
    destino.rename(destino.with_name("SCAN_TO_TEXT.antigo"))
    _instalar(tmp_path, _pacote(tmp_path / "v2", "2.0.0"))
    assert (destino / "config.json").read_text(encoding="utf-8") == '{"ping_min": 5}'
    assert (destino / "entrega.txt").read_bytes().endswith(b"VERSAO=2.0.0\r\n")
    assert not destino.with_name("SCAN_TO_TEXT.antigo").exists()


def test_recusa_pasta_alheia_no_destino(tmp_path):
    alheia = tmp_path / "RADIOTERAPIA_AI" / "SCAN_TO_TEXT"
    alheia.mkdir(parents=True)
    (alheia / "planilha.txt").write_bytes(b"de outra pessoa")
    with pytest.raises(inst.InstalacaoRecusada, match="SCAN_TO_TEXT"):
        _instalar(tmp_path, _pacote(tmp_path / "pacote"))
    assert [p.name for p in alheia.iterdir()] == ["planilha.txt"]


def test_recusa_pacote_incompleto(tmp_path):
    pacote = _pacote(tmp_path / "pacote")
    (pacote / "entrega.txt").write_bytes(b"PROJETO=OUTRO\r\nVERSAO=1\r\n")
    with pytest.raises(inst.InstalacaoRecusada, match="extraia"):
        _instalar(tmp_path, pacote)
    (pacote / "entrega.txt").write_bytes(b"PROJETO=SCAN_TO_TEXT\r\nVERSAO=1\r\n")
    (pacote / "python" / "pythonw.exe").unlink()
    with pytest.raises(inst.InstalacaoRecusada, match="extraia"):
        _instalar(tmp_path, pacote)
    assert not (tmp_path / "RADIOTERAPIA_AI").exists()


def test_recusa_pacote_extraido_dentro_da_instalacao(tmp_path):
    destino = _instalar(tmp_path, _pacote(tmp_path / "v1"))
    dentro = _pacote(destino / "Downloads" / "Scan-to-Text")
    with pytest.raises(inst.InstalacaoRecusada, match="dentro"):
        _instalar(tmp_path, dentro)


def test_sem_espaco_recusa_antes_de_copiar(tmp_path):
    with pytest.raises(inst.InstalacaoRecusada, match="espaco"):
        _instalar(tmp_path, _pacote(tmp_path / "pacote"), espaco_livre=lambda _: 10)
    assert not (tmp_path / "RADIOTERAPIA_AI" / "SCAN_TO_TEXT").exists()


def test_icacls_que_falha_so_avisa(tmp_path):
    chamadas = _Chamadas(codigo_icacls=5)
    destino = _instalar(tmp_path, _pacote(tmp_path / "pacote"), chamadas)
    assert (destino / "webview2" / "msedgewebview2.exe").is_file()
    assert any("webview2" in d.lower() and "aviso" in d.lower() for d in chamadas.ditos)
    assert chamadas.abertos == [destino]


def test_icacls_de_verdade_aceita_o_comando(tmp_path):
    pasta = tmp_path / "app"
    (pasta / "webview2").mkdir(parents=True)
    (pasta / "webview2" / "msedgewebview2.exe").write_bytes(b"MZ")
    assert inst.liberar_webview2(pasta)


def test_atalho_de_verdade_aponta_para_o_pythonw(tmp_path):
    alvo = tmp_path / "app" / "python" / "pythonw.exe"
    alvo.parent.mkdir(parents=True)
    alvo.write_bytes(b"MZ")
    lnk = tmp_path / "Scan-to-Text.lnk"
    inst.criar_atalho(lnk, alvo, "-m scan_to_text", tmp_path / "app", "Scan-to-Text")
    bruto = lnk.read_bytes()
    assert bruto[:4] == b"L\x00\x00\x00"
    assert "-m scan_to_text".encode("utf-16-le") in bruto
    assert b"pythonw.exe" in bruto or "pythonw.exe".encode("utf-16-le") in bruto


def test_pastas_de_atalho_sao_a_area_de_trabalho_e_o_menu_iniciar():
    desktop, menu = inst.pastas_de_atalho()
    assert desktop.is_dir()
    assert menu.name == "Braga HealthLab" and menu.parent.name.lower() in {"programs", "programas"}


# ---------------------------------------------------------------- desinstalação

def test_desinstalar_tira_app_atalhos_e_so_a_propria_chave(tmp_path):
    raiz = tmp_path / "RADIOTERAPIA_AI"
    raiz.mkdir()
    (raiz / "_instalados.json").write_text(json.dumps({"AUTO_CONTORNO": {"versao": "8.5"}}),
                                           encoding="utf-8")
    pacientes = tmp_path / "DESTINO" / "FULANA DE TESTE - 2026-10-25"
    pacientes.mkdir(parents=True)
    (pacientes / "COMPLETO.md").write_text("# FULANA DE TESTE\n", encoding="utf-8")
    destino = _instalar(tmp_path, _pacote(tmp_path / "pacote"))
    (destino / "config.json").write_text(json.dumps({"destinos": [str(tmp_path / "DESTINO")]}),
                                         encoding="utf-8")
    assert inst.desinstalar(raiz, [tmp_path / "desktop", tmp_path / "menu"], dizer=lambda *_: None)
    assert not destino.exists()
    assert not (tmp_path / "desktop" / "Scan-to-Text.lnk").exists()
    assert not (tmp_path / "menu" / "Scan-to-Text.lnk").exists()
    assert _registro(tmp_path) == {"AUTO_CONTORNO": {"versao": "8.5"}}
    assert (pacientes / "COMPLETO.md").read_text(encoding="utf-8") == "# FULANA DE TESTE\n"
    assert sorted(p.name for p in raiz.iterdir()) == ["_instalados.json"]


def test_desinstalar_com_app_aberto_recusa_e_nada_muda(tmp_path):
    destino = _instalar(tmp_path, _pacote(tmp_path / "pacote"))
    with open(destino / "python" / "pythonw.exe", "rb"):
        with pytest.raises(inst.InstalacaoRecusada, match="aberto"):
            inst.desinstalar(tmp_path / "RADIOTERAPIA_AI", [tmp_path / "desktop"],
                             dizer=lambda *_: None)
    assert (destino / "python" / "pythonw.exe").is_file()
    assert (tmp_path / "desktop" / "Scan-to-Text.lnk").is_file()
    assert "SCAN_TO_TEXT" in _registro(tmp_path)


def test_desinstalar_nao_apaga_pasta_alheia(tmp_path):
    alheia = tmp_path / "RADIOTERAPIA_AI" / "SCAN_TO_TEXT"
    alheia.mkdir(parents=True)
    (alheia / "planilha.txt").write_bytes(b"de outra pessoa")
    assert not inst.desinstalar(tmp_path / "RADIOTERAPIA_AI", [], dizer=lambda *_: None)
    assert (alheia / "planilha.txt").is_file()


# ---------------------------------------------------------------- raiz e entrada

def test_raiz_pela_variavel_pela_unidade_do_sistema_ou_reserva(tmp_path):
    assert inst.raiz_padrao({"RADAI_RAIZ_INSTALACAO": str(tmp_path)}) == tmp_path
    assert inst.raiz_padrao({"SystemDrive": "E:"}) == Path("E:\\RADIOTERAPIA_AI")
    assert inst.raiz_reserva({"LOCALAPPDATA": str(tmp_path)}) == tmp_path / "RADIOTERAPIA_AI"


def test_raiz_negada_recua_para_o_perfil_e_o_registro_guarda_onde_ficou(tmp_path, monkeypatch):
    negada = tmp_path / "sistema" / "RADIOTERAPIA_AI"
    ambiente = {"RADAI_RAIZ_INSTALACAO": str(negada), "LOCALAPPDATA": str(tmp_path / "local")}
    criar = Path.mkdir

    def mkdir(self, *args, **kwargs):
        # Sem administrador, a raiz da unidade pode negar a criação da pasta.
        if self == negada:
            raise PermissionError(13, "Acesso negado", str(self))
        return criar(self, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    raiz = inst.raiz_de_instalacao(ambiente)
    assert raiz == tmp_path / "local" / "RADIOTERAPIA_AI"
    chamadas = _Chamadas()
    destino = inst.instalar(_pacote(tmp_path / "pacote"), raiz, [tmp_path / "desktop"], HOJE,
                            executar=chamadas.executar, abrir=chamadas.abrir, dizer=chamadas.dizer,
                            espaco_livre=lambda _: 10 ** 12)
    assert destino == raiz / "SCAN_TO_TEXT" and (destino / "python" / "pythonw.exe").is_file()
    registro = json.loads((raiz / "_instalados.json").read_text(encoding="utf-8"))
    assert registro["SCAN_TO_TEXT"]["caminho"] == str(destino)
    assert not negada.exists()


def test_main_fora_de_um_pacote_para_e_registra_no_log(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RADAI_RAIZ_INSTALACAO", str(tmp_path / "raiz"))
    monkeypatch.setattr(inst, "pastas_de_atalho", lambda: [tmp_path / "desktop"])
    log = tmp_path / "INSTALL_LOG.txt"
    # Rodando do código-fonte, a pasta acima de app/ não é um pacote extraído.
    assert inst.main(["--log", str(log)]) == 1
    assert "PAROU" in capsys.readouterr().out
    assert "PAROU" in log.read_text(encoding="utf-8")
    assert not (tmp_path / "raiz" / "SCAN_TO_TEXT").exists()


# ---------------------------------------------------------------- INSTALL.cmd e LEIA-ME

def test_install_cmd_segue_a_regra_de_ambiente():
    texto = _cmd_de_ambiente(RAIZ / "packager" / "INSTALL.cmd")
    _saidas_pausadas(texto)
    assert "Scan-to-Text\\python\\python.exe" in texto
    assert "-m scan_to_text.instalar" in texto and "%*" in texto
    assert "INSTALL_LOG.txt" in texto
    assert "powershell" not in texto.lower()


def test_install_cmd_de_dentro_do_zip_explica_e_para(tmp_path):
    # Dois cliques dentro do .zip: o Windows extrai só o .cmd numa pasta temporária.
    cmd = tmp_path / "Temp1_SCAN_TO_TEXT_v1.zip" / "INSTALL.cmd"
    cmd.parent.mkdir()
    cmd.write_bytes((RAIZ / "packager" / "INSTALL.cmd").read_bytes())
    r = subprocess.run(["cmd.exe", "/d", "/c", str(cmd)], stdin=subprocess.DEVNULL,
                       capture_output=True, cwd=tmp_path, timeout=60)
    saida = r.stdout.decode("cp850", errors="replace")
    assert r.returncode == 1
    assert "EXTRAIDO" in saida and "Extrair tudo" in saida
    assert not (cmd.parent / "INSTALL_LOG.txt").exists()


def test_leia_me_e_ascii_crlf_e_explica_o_ciclo():
    bruto = (RAIZ / "packager" / "LEIA-ME.txt").read_bytes()
    assert bruto.isascii()
    assert b"\n" not in bruto.replace(b"\r\n", b"")
    texto = bruto.decode("ascii")
    for trecho in ("INSTALL.cmd", "Extrair tudo", "RADIOTERAPIA_AI\\SCAN_TO_TEXT", "config.json",
                   "desinstalar", "INSTALL_LOG.txt", "nao e dispositivo medico"):
        assert trecho in texto, trecho
