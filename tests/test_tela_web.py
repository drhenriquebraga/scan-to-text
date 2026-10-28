"""A tela: todo texto vem do JSON, sem emoji, CSP estrita, e as reservas da janela."""
import json
import re
import sys
import types
from html.parser import HTMLParser
from pathlib import Path

import pytest

from scan_to_text import config, preflight
from scan_to_text.tela import janela

WEB = Path(janela.__file__).resolve().parent / "web"
I18N = json.loads((WEB / "i18n" / "pt-BR.json").read_text(encoding="utf-8"))
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍"
                   "\U0001F1E6-\U0001F1FF]")
ATRIBUTOS_DE_TEXTO = ("title", "aria-label", "placeholder", "alt")


def _chave(dic, chave):
    for parte in chave.split("."):
        if not isinstance(dic, dict) or parte not in dic:
            return None
        dic = dic[parte]
    return dic


def _arquivos(*sufixos):
    return sorted(p for p in WEB.rglob("*") if p.suffix in sufixos)


class _Leitor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.pilha, self.textos, self.chaves, self.tags = [], [], [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append((tag, a))
        if tag not in ("meta", "link", "br", "input", "img", "path", "circle", "rect"):
            self.pilha.append(tag)
        if "data-i18n" in a:
            self.chaves.append(a["data-i18n"])
        for par in (a.get("data-i18n-attr") or "").split(";"):
            if par.strip():
                self.chaves.append(par.split(":", 1)[1].strip())

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.pilha and self.pilha[-1] == tag:
            self.pilha.pop()

    def handle_endtag(self, tag):
        while self.pilha:
            if self.pilha.pop() == tag:
                break

    def handle_data(self, data):
        if data.strip() and not (set(self.pilha) & {"script", "style"}):
            self.textos.append(data.strip())


@pytest.fixture(scope="module")
def pagina():
    leitor = _Leitor()
    leitor.feed((WEB / "index.html").read_text(encoding="utf-8"))
    return leitor


# ─── texto só do JSON ─────────────────────────────────────────────────────

def test_html_nao_tem_texto_visivel_escrito_a_mao(pagina):
    assert pagina.textos == []


def test_atributos_de_leitura_vem_do_json(pagina):
    for tag, a in pagina.tags:
        for atributo in ATRIBUTOS_DE_TEXTO:
            assert not a.get(atributo), (tag, atributo, a.get(atributo))


def test_toda_chave_do_html_existe_no_json(pagina):
    assert pagina.chaves
    faltando = [c for c in pagina.chaves if not isinstance(_chave(I18N, c), str)]
    assert faltando == []


def test_toda_chave_do_js_existe_no_json():
    usadas = set()
    for js in _arquivos(".js"):
        # Só a chave literal inteira; `t('erro.' + cod)` monta a chave em execução.
        usadas |= set(re.findall(r"\bt[la]?\(\s*'([a-z0-9_.]+)'\s*[,)]",
                                 js.read_text(encoding="utf-8")))
    assert usadas
    assert sorted(c for c in usadas if _chave(I18N, c) is None) == []


def test_avisos_do_preflight_tem_texto():
    fonte = Path(preflight.__file__).read_text(encoding="utf-8")
    chaves = set(re.findall(r'"(aviso\.[a-z_]+)"', fonte))
    assert chaves
    assert sorted(c for c in chaves if not isinstance(_chave(I18N, c), str)) == []


def test_erros_da_validacao_tem_texto():
    for codigo in (*config.ERROS, "idioma_desconhecido", "idioma_sem_regras",
                   "numero_invalido", "corpo_invalido"):
        assert isinstance(_chave(I18N, f"erro.{codigo}"), str), codigo


def test_aviso_local_do_js_tem_texto():
    usados = set()
    for js in _arquivos(".js"):
        usados |= set(re.findall(r"mostrarAvisoLocal\(\s*'([a-z0-9_]+)'",
                                 js.read_text(encoding="utf-8")))
    assert usados
    assert sorted(c for c in usados if not isinstance(_chave(I18N, f"aviso.{c}"), str)) == []


def test_textos_sao_impessoais():
    # A tela fala do app, e não por ele: "Não foi possível...", e não "Não consegui...".
    primeira_pessoa = re.compile(r"\b(consegui|pude|tentei|encontrei|achei|abri|gravei|salvei|"
                                 r"li|vou|estou)\b", re.I)
    texto = json.dumps(I18N, ensure_ascii=False)
    assert not primeira_pessoa.search(texto), primeira_pessoa.search(texto)


def _funcao(js: str, nome: str) -> str:
    achado = re.search(r"\n  function " + nome + r"\([^)]*\) \{\n(.*?)\n  \}\n", js, re.S)
    assert achado, nome
    return achado.group(1)


def test_troca_de_idioma_avisa_quando_o_motor_recusa():
    # O POST passa pelo salvar_config com a configuração inteira: uma origem
    # fora do ar recusa a troca, e a tela não pode fingir que gravou.
    corpo = _funcao((WEB / "app.js").read_text(encoding="utf-8"), "escolherIdioma")
    assert ".catch(function () {})" not in corpo
    assert "r.erros" in corpo and "idiomaNaoSalvo" in corpo


def _app_js() -> str:
    return (WEB / "app.js").read_text(encoding="utf-8")


def _chaves_do_objeto(js: str, nome: str) -> set[str]:
    achado = re.search(r"\bvar " + nome + r" = \{(.*?)\};", js, re.S)
    assert achado, nome
    return set(re.findall(r"'?([\w.]+)'?\s*:\s*true", achado.group(1)))


def test_aviso_que_bloqueia_tem_texto_e_proximo_passo():
    js = _app_js()
    for cod in _chaves_do_objeto(js, "AVISO_BLOQUEIA") | _chaves_do_objeto(js, "AVISO_PAUSA"):
        chave = cod if cod.startswith("aviso.") else "aviso." + cod
        assert isinstance(_chave(I18N, chave), str), cod
        assert isinstance(_chave(I18N, chave + "_acao"), str), cod


def test_leitor_indisponivel_bloqueia_e_pausa_a_leitura():
    js = _app_js()
    assert "leitor_indisponivel" in _chaves_do_objeto(js, "AVISO_BLOQUEIA")
    assert "leitor_indisponivel" in _chaves_do_objeto(js, "AVISO_PAUSA")


def test_topo_fica_escondido_atras_do_aviso_do_motor():
    # Atrás do aviso do motor, engrenagem e Voltar trocariam a vista escondida.
    # Quando o motor volta, inclusive na nova tentativa da primeira carga, os
    # dois reaparecem conforme a vista.
    js = _app_js()
    topo = _funcao(js, "pintarTopo")
    for botao in ("bt-engrenagem", "bt-voltar"):
        linha = next(x for x in topo.splitlines() if botao in x)
        assert "parado" in linha, linha
    for nome in ("mostrarVista", "mostrarMotorParado", "motorVoltou", "carregarInicio"):
        corpo = _funcao(js, nome)
        assert "pintarTopo();" in corpo, nome
        assert "'bt-engrenagem').hidden" not in corpo, nome
        assert "'bt-voltar').hidden" not in corpo, nome
    carga = _funcao(js, "carregarInicio")
    assert carga.index("parado = '';") < carga.index("pintarTopo();")


def test_clique_duplo_no_start_nao_liga_e_desliga():
    js = _app_js()
    assert "function alternar(ev) {" in js
    alternar = _funcao(js, "alternar")
    assert "ev.detail > 1" in alternar and "surdoAte" in alternar
    carencia = int(re.search(r"\bvar CARENCIA_MS = (\d+);", js).group(1))
    assert 500 <= carencia <= 1000
    # A carência vale para toda troca de estado vinda do motor: a resposta
    # do START/STOP e a consulta periódica.
    assert "CARENCIA_MS" in _funcao(js, "receberEstado")
    assert "receberEstado(" in alternar
    assert "receberEstado(" in _funcao(js, "perguntarEstado")


def test_seletor_cancelado_nao_abre_o_painel_de_colar():
    corpo = _funcao(_app_js(), "escolherPasta")
    assert "r.sem_seletor" in corpo
    for linha in corpo.splitlines():
        if "abrirPainel(nome, true)" in linha:
            assert "r.sem_seletor" in linha or ".catch" in linha, linha


def test_erro_de_pasta_aninhada_fala_da_pasta_nova():
    js = _app_js()
    bloco = re.search(r"\bvar ERRO_DA_PASTA_NOVA = \{(.*?)\};", js, re.S)
    assert bloco
    mapa = dict(re.findall(r"'(\w+:\w+)'\s*:\s*'(\w+)'", bloco.group(1)))
    assert mapa == {"origens:destino_em_origem": "origem_contem_destino",
                    "destinos:origem_em_destino": "destino_contem_origem"}
    for chave in mapa.values():
        texto = _chave(I18N, "erro_pasta." + chave)
        assert isinstance(texto, str) and "{caminho}" in texto, chave
    assert "ERRO_DA_PASTA_NOVA[" in _funcao(js, "textoErroPasta")
    assert "textoErroPasta(nome, " in _funcao(js, "incluir")


def test_pasta_repetida_tem_texto():
    texto = _chave(I18N, "erro_pasta.pasta_repetida")
    assert isinstance(texto, str) and "{caminho}" in texto


def test_conta_do_dia_separa_os_ilegiveis():
    resumo = _funcao(_app_js(), "pintarResumo")
    # "Nenhum documento novo hoje" só quando nenhum arquivo chegou: ao lado de
    # um arquivo ilegível, ela se contradiz.
    linha = next(x for x in resumo.splitlines() if "inicio.hoje_nada" in x)
    assert "h.arquivos" in linha, linha
    assert "h.ilegiveis" in resumo
    for chave in ("inicio.ilegiveis", "inicio.ilegiveis_um", "inicio.hoje_sem_texto"):
        assert isinstance(_chave(I18N, chave), str), chave


def test_idioma_unico_so_quando_o_gravado_esta_entre_as_opcoes():
    # Código gravado fora das opções (antigo, ou editado à mão) não vira a
    # frase de idioma único com outro idioma: o controle aparece para a troca.
    corpo = _funcao(_app_js(), "pintarIdiomasDoc")
    assert "|| opcoes[0]" not in corpo
    linha = next(x for x in corpo.splitlines() if "unico =" in x)
    assert "&& !!atual" in linha, linha
    assert re.search(r"\batual = opcoes\.filter\(.*docs\[0\]", corpo)


def test_batimento_liga_antes_da_carga_dos_textos():
    # Fechar a janela durante a carga também precisa chegar ao motor.
    js = (WEB / "app.js").read_text(encoding="utf-8")
    iniciar = _funcao(js, "iniciar")
    assert iniciar.index("batimento();") < iniciar.index("carregarInicio();")
    assert "batimento()" not in _funcao(js, "carregarInicio")


def test_campos_numericos_aceitam_o_mesmo_que_o_config(pagina):
    # Valor que o config.json aceita (0.5 minuto, digitado à mão) não pode
    # abrir inválido na tela e travar o Salvar de tudo.
    campos = {a["data-campo"]: a for t, a in pagina.tags if t == "input" and "data-campo" in a}
    assert set(campos) == set(config.LIMITES) - {"versao_config"}
    for chave, a in campos.items():
        minimo, inteiro = config.LIMITES[chave]
        assert float(a["data-minimo"]) == minimo and float(a["min"]) == minimo, chave
        assert ("data-decimal" in a) is (not inteiro), chave
        assert a["step"] == ("1" if inteiro else "any"), chave
        assert a["inputmode"] == ("numeric" if inteiro else "decimal"), chave
    validar = _funcao((WEB / "app.js").read_text(encoding="utf-8"), "validarNumero")
    assert "data-decimal" in validar and "config.leitura.invalido_decimal" in validar


def test_js_nao_tem_texto_de_idioma_fora_de_comentario():
    for js in _arquivos(".js"):
        codigo = re.sub(r"/\*.*?\*/", "", js.read_text(encoding="utf-8"), flags=re.S)
        codigo = re.sub(r"(?m)^\s*//.*$", "", codigo)
        assert not re.search(r"[^\x00-\x7f]", codigo), js.name


def test_start_e_stop_iguais_em_todo_idioma():
    assert I18N["botao"] == {"start": "START", "stop": "STOP"}


def test_referencias_creditam_os_efeitos_e_o_seletor():
    itens = I18N["config"]["referencias"]["itens"]
    texto = json.dumps(itens, ensure_ascii=False)
    for nome in ("Transitions.dev", "matrix-dot-loader", "shimmer-text", "Local Suite",
                 "Docling", "RapidOCR", "pypdfium2", "pywebview", "FastAPI",
                 "pi-heif", "WebView2", "Visual C++"):
        assert nome in texto, nome
    assert all({"nome", "papel", "licenca"} <= set(i) for i in itens)


def test_referencias_nao_creditam_quem_saiu_do_pacote():
    # A orientação da página ficou com o classificador de linha do RapidOCR.
    texto = json.dumps(I18N["config"]["referencias"], ensure_ascii=False)
    assert "Tesseract" not in texto


# ─── sem emoji, CSP estrita, sem HTML dinâmico ────────────────────────────

def test_nenhum_emoji_na_tela():
    for arquivo in _arquivos(".html", ".css", ".js", ".json"):
        achado = EMOJI.search(arquivo.read_text(encoding="utf-8"))
        assert not achado, (arquivo.name, achado and hex(ord(achado.group())))


def test_csp_estrita_no_html(pagina):
    csp = next(a["content"] for t, a in pagina.tags
               if t == "meta" and a.get("http-equiv", "").lower() == "content-security-policy")
    diretivas = {d.split()[0]: d.split()[1:] for d in csp.split(";") if d.strip()}
    assert diretivas["default-src"] == ["'none'"]
    assert diretivas["script-src"] == ["'self'"]
    assert diretivas["style-src"] == ["'self'"]
    assert diretivas["connect-src"] == ["'self'"]
    assert "unsafe" not in csp and "http:" not in csp and "https:" not in csp


def test_sem_script_embutido_nem_estilo_embutido(pagina):
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert "<style" not in html
    for tag, a in pagina.tags:
        assert "style" not in a, tag
        assert not any(k.startswith("on") for k in a), (tag, a)


def test_html_dinamico_so_no_seletor_copiado():
    for js in _arquivos(".js"):
        if js.name != "seletor.js":
            assert "innerHTML" not in js.read_text(encoding="utf-8"), js.name


def test_sem_recurso_externo():
    for arquivo in _arquivos(".html", ".css", ".js"):
        texto = arquivo.read_text(encoding="utf-8")
        assert not re.search(r"(src|href|url)\s*[=(]\s*['\"]?(https?:)?//", texto), arquivo.name
        assert "@import" not in texto, arquivo.name


# ─── movimento, direção e efeitos ─────────────────────────────────────────

def test_movimento_reduzido_respeitado_em_todo_css_com_animacao():
    for css in _arquivos(".css"):
        texto = css.read_text(encoding="utf-8")
        if "animation" in texto or "transition" in texto:
            assert "prefers-reduced-motion" in texto, css.name


def test_css_usa_propriedades_logicas_para_o_arabe():
    fisicas = re.compile(r"(?<![-\w])(margin-left|margin-right|padding-left|padding-right|"
                         r"border-left|border-right|left|right|float)\s*:|"
                         r"text-align\s*:\s*(left|right)")
    for css in _arquivos(".css"):
        texto = re.sub(r"/\*.*?\*/", "", css.read_text(encoding="utf-8"), flags=re.S)
        assert not fisicas.search(texto), (css.name, fisicas.search(texto))


def test_efeitos_do_transitions_dev_com_credito():
    css = (WEB / "efeitos.css").read_text(encoding="utf-8")
    assert "Transitions.dev" in css
    assert ".t-matrix" in css and ".t-shimmer" in css
    assert "@keyframes t-matrix-pulse" in css and "@keyframes t-shimmer" in css


def test_seletor_com_nota_de_origem():
    for nome in ("seletor.js", "seletor.css"):
        texto = (WEB / nome).read_text(encoding="utf-8")
        assert "radai_languages.py" in texto and "Apache-2.0" in texto, nome


def test_espera_do_transitions_dev_com_credito():
    for nome in ("status-espera.css", "status-espera.js"):
        assert "Transitions.dev" in (WEB / nome).read_text(encoding="utf-8"), nome


# ─── identidade e linha de conexão ────────────────────────────────────────

def _folhas_e_scripts(pagina):
    folhas = [a["href"] for t, a in pagina.tags if t == "link" and a.get("rel") == "stylesheet"]
    scripts = [a["src"] for t, a in pagina.tags if t == "script"]
    return folhas, scripts


def test_marca_mostra_so_o_nome_do_app(pagina):
    assert I18N["marca"] == {"nome": "Scan-to-", "sufixo": "Text"}
    assert "marca-sub" not in (WEB / "index.html").read_text(encoding="utf-8")
    # A origem fica só nos créditos de Referências.
    sem_creditos = json.loads(json.dumps(I18N))
    del sem_creditos["config"]["referencias"]
    assert "radioterapia" not in json.dumps(sem_creditos, ensure_ascii=False).lower()


def test_identidade_e_espera_carregam_depois_das_folhas_originais(pagina):
    folhas, scripts = _folhas_e_scripts(pagina)
    assert folhas == ["estilo.css", "seletor.css", "efeitos.css",
                      "identidade-local-suite.css", "status-espera.css"]
    assert scripts == ["seletor.js", "efeitos.js", "status-espera.js", "app.js"]


def test_identidade_so_muda_cor():
    css = re.sub(r"/\*.*?\*/", "", (WEB / "identidade-local-suite.css").read_text(encoding="utf-8"),
                 flags=re.S)
    cor = re.compile(r"(--[\w-]+|color|background|background-color|border-color|"
                     r"border-block-(start|end)-color|stroke)")
    for corpo in re.findall(r"\{([^{}]*)\}", css):
        for decl in filter(str.strip, corpo.split(";")):
            prop = decl.split(":", 1)[0].strip()
            assert cor.fullmatch(prop), prop


def test_linha_de_conexao_so_apresenta_a_consulta_que_ja_existe():
    espera = (WEB / "status-espera.js").read_text(encoding="utf-8")
    for proibido in ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon",
                     "/api/"):
        assert proibido not in espera, proibido
    consulta = _funcao(_app_js(), "perguntarEstado")
    assert consulta.index("ESPERA.iniciarConsulta()") < consulta.index("api('GET', 'estado')")
    assert "ESPERA.resposta(e, performance.now() - inicio)" in consulta
    assert "ESPERA.falha(err.status)" in consulta
    # Uma consulta só: a linha não ganha temporizador de rede próprio.
    assert _app_js().count("api('GET', 'estado')") == 1


def test_linha_de_conexao_tem_texto():
    espera = (WEB / "status-espera.js").read_text(encoding="utf-8")
    usadas = set(re.findall(r"traduzir\(\s*'([a-z_]+)'", espera))
    usadas |= set(re.findall(r"\b(verificando|ativa|lenta|instavel|inativa)\b", espera))
    assert {"verificando", "ativa", "lenta", "instavel", "inativa"} <= usadas
    assert sorted(c for c in usadas if not isinstance(_chave(I18N, "conexao." + c), str)) == []
    assert "STTTraduzirStatus" in _app_js()
    assert "ESPERA.atualizar()" in _funcao(_app_js(), "repintar")


def test_start_e_o_unico_com_profundidade():
    css = (WEB / "estilo.css").read_text(encoding="utf-8")
    blocos = re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S))
    for seletor, corpo in blocos:
        if re.search(r"box-shadow\s*:(?![^;]*\bnone\b)[^;]*\d+px\s+\d+px", corpo):
            assert ".start" in seletor, seletor.strip()


# ─── janela e reservas ────────────────────────────────────────────────────

def test_webview2_do_pacote_achado_pela_marca(tmp_path):
    raiz = tmp_path / "Scan-to-Text"
    fundo = raiz / "app" / "scan_to_text" / "tela"
    fundo.mkdir(parents=True)
    assert janela.raiz_webview2(fundo) is None
    (raiz / "webview2").mkdir()
    (raiz / "webview2" / "msedgewebview2.exe").write_bytes(b"")
    assert janela.raiz_webview2(fundo) == raiz / "webview2"


def test_webview2_numa_subpasta_da_cab(tmp_path):
    versao = tmp_path / "webview2" / "Microsoft.WebView2.FixedVersionRuntime.130.x64"
    versao.mkdir(parents=True)
    (versao / "msedgewebview2.exe").write_bytes(b"")
    assert janela.raiz_webview2(tmp_path / "app") == versao


def test_comando_do_navegador_em_modo_app(tmp_path):
    cmd = janela.comando_navegador("msedge.exe", "http://127.0.0.1:5000/#t=x", tmp_path)
    assert cmd[0] == "msedge.exe"
    assert "--app=http://127.0.0.1:5000/#t=x" in cmd
    assert f"--user-data-dir={tmp_path}" in cmd
    assert "--disable-background-networking" in cmd
    assert not any("remote-debugging" in c for c in cmd)


def test_console_nao_abre_nada(monkeypatch):
    chamadas = []
    monkeypatch.setattr(janela, "_abrir_janela", lambda url: chamadas.append(url))
    janela.abrir("http://127.0.0.1:1/", "console", lambda: chamadas.append("fechou"))
    assert chamadas == []


def test_janela_fecha_chama_ao_fechar(monkeypatch):
    chamadas = []
    monkeypatch.setattr(janela, "_abrir_janela", lambda url: chamadas.append(url))
    janela.abrir("http://127.0.0.1:1/", "janela", lambda: chamadas.append("fechou"))
    assert chamadas == ["http://127.0.0.1:1/", "fechou"]


def test_janela_que_nao_abre_cai_no_navegador(monkeypatch, tmp_path):
    lancados, fechou = [], []

    def falha(_url):
        raise RuntimeError("WebView2 ausente")

    monkeypatch.setattr(janela, "_abrir_janela", falha)
    monkeypatch.setattr(janela, "achar_navegador", lambda: "chrome.exe")
    monkeypatch.setattr(janela, "_pasta_dados", lambda: tmp_path)
    monkeypatch.setattr(janela.subprocess, "Popen", lambda cmd, **_k: lancados.append(cmd))
    janela.abrir("http://127.0.0.1:1/", "janela", lambda: fechou.append(1))
    assert lancados and lancados[0][0] == "chrome.exe" and fechou == []


def test_sem_navegador_levanta_tela_indisponivel(monkeypatch):
    monkeypatch.setattr(janela, "achar_navegador", lambda: None)
    with pytest.raises(janela.TelaIndisponivel):
        janela.abrir("http://127.0.0.1:1/", "navegador", lambda: None)


def test_degrau_desconhecido_e_erro():
    with pytest.raises(ValueError):
        janela.abrir("http://127.0.0.1:1/", "tela_cheia", lambda: None)


def test_escolher_pasta_sem_janela_e_none():
    assert janela.escolher_pasta() is None


def test_escolher_pasta_cancelado_e_texto_vazio(monkeypatch):
    # O diálogo do pywebview devolve None no Cancelar: sem esta distinção, a
    # tela diria que o seletor do Windows não abriu.
    class Janela:
        def __init__(self, escolha):
            self.escolha = escolha

        def create_file_dialog(self, _tipo):
            return self.escolha

    falso = types.SimpleNamespace(FileDialog=types.SimpleNamespace(FOLDER=20))
    monkeypatch.setitem(sys.modules, "webview", falso)
    for escolha in (None, ()):
        monkeypatch.setitem(janela._ESTADO, "janela", Janela(escolha))
        assert janela.escolher_pasta() == ""
    monkeypatch.setitem(janela._ESTADO, "janela", Janela(("D:\\pacientes",)))
    assert janela.escolher_pasta() == "D:\\pacientes"
