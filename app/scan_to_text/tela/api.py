"""API local da tela.

FastAPI em 127.0.0.1, porta efêmera. Quem sobe o servidor (`principal`)
escolhe a porta, põe `http://127.0.0.1:<porta>` em `origens_permitidas` e
abre a tela em `http://127.0.0.1:<porta>/#t=<token>`. O token vai no
FRAGMENTO, que o navegador não manda ao servidor nem grava no histórico de
rede; a página o guarda na `sessionStorage` e o apaga da barra de endereço.

Três portões, em todo pedido:
  * `Host`: só o nome e a porta das origens permitidas. Sem isto, uma página
    cujo domínio passa a resolver para 127.0.0.1 (rebinding de DNS) leria as
    respostas como se fosse a própria tela;
  * `Origin`: presente e estranho, recusa. Ausente passa, porque navegador
    sempre o manda em pedido de outra origem;
  * token em `X-Token` em toda rota `/api/`. O batimento aceita o token no
    corpo também: `navigator.sendBeacon` não deixa pôr cabeçalho.

`origens_permitidas` é lido a cada pedido, e não copiado: a porta efêmera só
é conhecida depois de o servidor subir, e o conjunto recebe a origem então.

Caminho de pasta só no corpo de POST, nunca na URL: URL vai para log de
servidor, histórico e cabeçalho `Referer`.

Formato de `Motor.estado()` que a tela entende: `estado` ("parado",
"ocioso", "trabalhando" ou "parando"), `configurado` (a API o calcula de
`config()` quando falta), `avisos` (códigos: "origem_inacessivel",
"destino_inacessivel", "disco_cheio", "leitor_indisponivel" ou chaves
"aviso.*" do preflight), `hoje` (a conta do dia: `arquivos`, `documentos`,
`desconhecidos`, `ilegiveis` e `ultimo`) e, opcional, `versao`.

O batimento da página (a cada 5 s) e a carência de 20 s depois do aviso de
saída ficam em `app.state.batimento`; no modo navegador, o `principal`
consulta `abandonada()` e encerra quando ela responde True. Antes
do primeiro pulso, a espera de 60 s conta da criação do app: ele é criado logo
antes de a tela abrir.

`POST /api/config` só repassa ao motor os campos do `config.PADRAO`, menos
`versao_config`, e recusa número fora dos limites de `config.LIMITES`
("numero_invalido") e lista de pastas que não seja lista de textos (400,
"corpo_invalido").
"""
import ctypes
import hmac
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from scan_to_text import config, nuvem, pacote

PASTA_WEB = Path(__file__).resolve().parent / "web"
CABECALHO_TOKEN = "X-Token"
BATIMENTO_S = 5.0
CARENCIA_S = 20.0
ESPERA_INICIAL_S = 60.0
TETO_CORPO = 1 << 20
MAX_PASTAS = 2000
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; worker-src 'self'; font-src 'self'; base-uri 'none'; "
       "form-action 'none'; frame-ancestors 'none'")
_OCULTA_OU_SISTEMA = 0x2 | 0x4
_UNIDADE_DE_REDE = 4
_SEM_DIALOGO_DE_ERRO = 0x1

# ─── idiomas ─────────────────────────────────────────────────────────────
# Porte do seletor de idioma do local-suite v8.3 (`app/radai_languages.py`:
# `IDIOMAS`, `BANDEIRAS`, `bandeira`, `canonico`, `ALIAS_IDIOMA`; commit
# 55cffa9, Apache-2.0). A ordem é dado, e não resultado de ordenação: pelo
# endônimo, sem acento, com as escritas não latinas depois, em ordem de
# código. Ordenar em execução dependeria da collation da máquina.
#
# As bandeiras são desenho próprio em SVG, e não emoji: o Windows 10 não tem
# glifo de bandeira e desenharia duas letras soltas. A bandeira indica a
# língua, e não o país: o inglês leva o Reino Unido, o árabe leva a Arábia
# Saudita (a convenção mais comum para a língua) e o português leva o Brasil
# porque o código é `pt-BR`. Sem `id`, sem texto, sem `href`: a mesma bandeira
# aparece duas vezes na página (no botão e na lista).
IDIOMAS = [
    ("de",       u"DE", u"Deutsch", False),
    ("en",       u"EN", u"English", False),
    ("es",       u"ES", u"Español", False),
    ("fr",       u"FR", u"Français", False),
    ("it",       u"IT", u"Italiano", False),
    ("pl",       u"PL", u"Polski", False),
    ("pt-BR",    u"PT", u"Português (Brasil)", False),
    ("ar",       u"AR", u"العربية", True),
    ("bn",       u"BN", u"বাংলা", False),
    ("ko",       u"KO", u"한국어", False),
    ("zh-CN",    u"ZH", u"中文", False),
    ("ja",       u"JA", u"日本語", False),
]

BANDEIRAS = {
    "de": (u'<path fill="#000" d="M0 0h30v6.67H0z"/>'
           u'<path fill="#DD0000" d="M0 6.67h30v6.66H0z"/>'
           u'<path fill="#FFCE00" d="M0 13.33h30V20H0z"/>'),
    "en": (u'<path fill="#012169" d="M0 0h30v20H0z"/>'
           u'<path stroke="#fff" stroke-width="4" d="M0 0l30 20M30 0L0 20"/>'
           u'<path stroke="#C8102E" stroke-width="1.4" d="M0 0l30 20M30 0L0 20"/>'
           u'<path stroke="#fff" stroke-width="6.6" d="M15 0v20M0 10h30"/>'
           u'<path stroke="#C8102E" stroke-width="4" d="M15 0v20M0 10h30"/>'),
    "es": (u'<path fill="#AA151B" d="M0 0h30v20H0z"/>'
           u'<path fill="#F1BF00" d="M0 5h30v10H0z"/>'),
    "fr": (u'<path fill="#fff" d="M0 0h30v20H0z"/>'
           u'<path fill="#002654" d="M0 0h10v20H0z"/>'
           u'<path fill="#ED2939" d="M20 0h10v20H20z"/>'),
    "it": (u'<path fill="#fff" d="M0 0h30v20H0z"/>'
           u'<path fill="#009246" d="M0 0h10v20H0z"/>'
           u'<path fill="#CE2B37" d="M20 0h10v20H20z"/>'),
    "pl": (u'<path fill="#fff" d="M0 0h30v10H0z"/>'
           u'<path fill="#DC143C" d="M0 10h30v10H0z"/>'),
    "pt-BR": (u'<path fill="#009C3B" d="M0 0h30v20H0z"/>'
              u'<path fill="#FFDF00" d="M15 2.4L27.4 10 15 17.6 2.6 10z"/>'
              u'<circle fill="#002776" cx="15" cy="10" r="4.9"/>'
              u'<path fill="none" stroke="#fff" stroke-width=".9" '
              u'd="M10.3 8.7c3.2-.7 6.6-.1 9.4 1.9"/>'),
    "ar": (u'<path fill="#006C35" d="M0 0h30v20H0z"/>'
           u'<path fill="none" stroke="#fff" stroke-width=".9" '
           u'stroke-linecap="round" d="M8 6.5c1-1 2 1 3 0s2 1 3 0 2 1 3 0 '
           u'2 1 3 0 2 1 3 0M9 9.6c1-1 2 1 3 0s2 1 3 0 2 1 3 0 2 1 3 0"/>'
           u'<path fill="none" stroke="#fff" stroke-width=".9" '
           u'stroke-linecap="round" d="M8.5 14h13M20.5 12.8v2.4"/>'),
    "bn": (u'<path fill="#006A4E" d="M0 0h30v20H0z"/>'
           u'<circle fill="#F42A41" cx="13.5" cy="10" r="6"/>'),
    "ko": (u'<path fill="#fff" d="M0 0h30v20H0z"/>'
           u'<g transform="rotate(33.69 15 10)">'
           u'<path fill="#CD2E3A" d="M10 10a5 5 0 0 1 10 0z"/>'
           u'<path fill="#0047A0" d="M10 10a5 5 0 0 0 10 0z"/>'
           u'<circle fill="#CD2E3A" cx="12.5" cy="10" r="2.5"/>'
           u'<circle fill="#0047A0" cx="17.5" cy="10" r="2.5"/></g>'
           u'<g stroke="#000" stroke-width=".7" fill="none">'
           u'<path transform="translate(6.3 4.2) rotate(-56.31)" '
           u'd="M-1.7-1.1h3.4M-1.7 0h3.4M-1.7 1.1h3.4"/>'
           u'<path transform="translate(23.7 15.8) rotate(-56.31)" '
           u'd="M-1.7-1.1h3.4M-1.7 0h3.4M-1.7 1.1h3.4"/>'
           u'<path transform="translate(23.7 4.2) rotate(56.31)" '
           u'd="M-1.7-1.1h3.4M-1.7 0h3.4M-1.7 1.1h3.4"/>'
           u'<path transform="translate(6.3 15.8) rotate(56.31)" '
           u'd="M-1.7-1.1h3.4M-1.7 0h3.4M-1.7 1.1h3.4"/></g>'),
    "zh-CN": (u'<path fill="#EE1C25" d="M0 0h30v20H0z"/>'
              u'<g fill="#FFFF00">'
              u'<path d="M5 2L5.67 4.07 7.85 4.07 6.09 5.35 6.76 7.43 5 6.15 '
              u'3.24 7.43 3.91 5.35 2.15 4.07 4.33 4.07z"/>'
              u'<path d="M9.14 2.51L9.62 1.97 9.25 1.34 9.91 1.63 10.39 1.08 '
              u'10.33 1.8 11 2.09 10.29 2.25 10.22 2.97 9.85 2.35z"/>'
              u'<path d="M11.01 4.14L11.66 3.82 11.56 3.1 12.07 3.62 12.72 '
              u'3.3 12.38 3.95 12.88 4.47 12.17 4.34 11.83 4.99 11.73 4.27z"/>'
              u'<path d="M11.04 6.73L11.76 6.7 11.96 6 12.21 6.68 12.94 6.66 '
              u'12.37 7.1 12.62 7.79 12.01 7.38 11.44 7.83 11.64 7.13z"/>'
              u'<path d="M9.22 8.38L9.9 8.63 10.35 8.06 10.32 8.79 11 9.05 '
              u'10.3 9.24 10.26 9.96 9.87 9.36 9.16 9.55 9.62 8.98z"/></g>'),
    "ja": (u'<path fill="#fff" d="M0 0h30v20H0z"/>'
           u'<circle fill="#BC002D" cx="15" cy="10" r="6"/>'),
}

_SVG_BANDEIRA = (u'<svg viewBox="0 0 30 20" preserveAspectRatio="none" '
                 u'aria-hidden="true" focusable="false">%s</svg>')

# Códigos gravados por versões antigas continuam valendo: renomear código de
# idioma é migração, e a escolha antiga cairia no padrão sem aviso.
ALIAS_IDIOMA = {"zh": "zh-CN", "pt": "pt-BR", "zh-Hans": "zh-CN"}


def canonico(cod) -> str:
    c = str(cod or "").strip()
    if not c:
        return ""
    if c in {x[0] for x in IDIOMAS}:
        return c
    return ALIAS_IDIOMA.get(c, "")


def bandeira(cod) -> str:
    tracos = BANDEIRAS.get(canonico(cod) or "")
    return (_SVG_BANDEIRA % tracos) if tracos else ""


# ─── motor e batimento ───────────────────────────────────────────────────

class Motor(Protocol):
    def estado(self) -> dict: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def config(self) -> dict: ...
    def salvar_config(self, cfg: dict) -> list[str]: ...
    def escolher_pasta(self) -> str | None: ...  # None sem seletor; "" cancelado
    def conferir_pasta(self, caminho: str, papel: str, origens: list[str],
                       destinos: list[str]) -> list[str]: ...
    def log_recente(self) -> list[str]: ...
    def salvar_log(self, destino: str) -> str: ...


class Batimento:
    """Sinal de vida da página no modo navegador.

    Antes do primeiro pulso a página ainda está carregando: a espera é maior,
    mas tem teto, porque um navegador fechado ou morto durante a carga
    deixaria o motor rodando sem janela. Ela conta da criação do app e
    recomeça a cada pedido da página que chega antes do primeiro pulso. Sem
    aviso de saída (navegador morto), o abandono conta depois do batimento que
    faltou; com aviso (`pagehide`), a carência corre desde o aviso, tempo para
    um F5 voltar.
    """

    def __init__(self, relogio: Callable[[], float] = time.monotonic):
        self._relogio = relogio
        self._trava = threading.Lock()
        self._ultimo: float | None = None
        self._saida: float | None = None
        self._visto = relogio()

    def visto(self) -> None:
        with self._trava:
            if self._ultimo is None:
                self._visto = self._relogio()

    def pulso(self) -> None:
        with self._trava:
            self._ultimo, self._saida = self._relogio(), None

    def saindo(self) -> None:
        with self._trava:
            self._saida = self._relogio()

    def abandonada(self) -> bool:
        with self._trava:
            agora = self._relogio()
            if self._saida is not None:
                return agora - self._saida >= CARENCIA_S
            if self._ultimo is None:
                return agora - self._visto >= ESPERA_INICIAL_S
            return agora - self._ultimo >= BATIMENTO_S + CARENCIA_S


# ─── app ─────────────────────────────────────────────────────────────────

class _Recusa(Exception):
    def __init__(self, status: int, codigo: str):
        super().__init__(codigo)
        self.status, self.codigo = status, codigo


def _erro(status: int, codigo: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo})


async def _objeto(request: Request) -> dict | None:
    dados = b""
    async for parte in request.stream():
        dados += parte
        if len(dados) > TETO_CORPO:
            raise _Recusa(413, "corpo_grande")
    try:
        valor = json.loads(dados.decode("utf-8-sig")) if dados.strip() else None
    except (UnicodeError, json.JSONDecodeError):
        return None
    return valor if isinstance(valor, dict) else None


def _lista_de_textos(valor) -> list[str]:
    return [v.strip() for v in valor if isinstance(v, str) and v.strip()] \
        if isinstance(valor, list) else []


# O que a tela pode mudar. `versao_config` é do app, e chave fora do PADRAO
# iria parar no config.json da estação pelas mãos do motor.
_EDITAVEIS = frozenset(config.PADRAO) - {"versao_config"}


def _pastas_do_corpo(valor) -> list[str]:
    # Texto no lugar da lista viraria lista vazia, e remover pasta é ação
    # explícita (lista vazia): o corpo é recusado inteiro.
    if not isinstance(valor, list) or not all(isinstance(v, str) for v in valor):
        raise _Recusa(400, "corpo_invalido")
    return _lista_de_textos(valor)


def _sem_repetidas(pastas: list[str]) -> list[str]:
    # Comparadas já na forma UNC: a letra mapeada que o seletor devolve e o
    # UNC que a lista guarda são a mesma pasta, como a diferença de
    # maiúsculas e a barra no fim.
    vistas, saida = set(), []
    for pasta in pastas:
        chave = pasta.replace("/", "\\").rstrip("\\").casefold()
        if chave not in vistas:
            vistas.add(chave)
            saida.append(pasta)
    return saida


def _mesclar(atual: dict, corpo: dict, raiz_packs: Path) -> tuple[dict, list[str]]:
    corpo = {k: v for k, v in corpo.items() if k in _EDITAVEIS}
    novo, erros = {**atual, **corpo}, []
    for chave in ("origens", "destinos"):
        if chave in corpo:
            novo[chave] = _sem_repetidas([config.para_unc(p)
                                          for p in _pastas_do_corpo(corpo[chave])])
    # Os limites são os da leitura do config.json: o motor não pode receber
    # o que a próxima leitura do arquivo trocaria pelo padrão.
    if any(not config.numero_valido(k, corpo[k]) for k in config.LIMITES if k in corpo):
        erros.append("numero_invalido")
    if "idioma_interface" in corpo:
        cod = canonico(corpo["idioma_interface"])
        if cod:
            novo["idioma_interface"] = cod
        else:
            erros.append("idioma_desconhecido")
    if "idiomas_documentos" in corpo:
        brutos = _lista_de_textos(corpo["idiomas_documentos"])
        cods = [canonico(c) for c in brutos]
        if not cods or not all(cods):
            erros.append("idioma_desconhecido")
        # Idioma de documento sem pacote de regras deixaria o motor sem rótulo
        # de nome e de data: a escolha é recusada, e não aceita em silêncio.
        if any(c and not (raiz_packs / c).is_dir() for c in cods):
            erros.append("idioma_sem_regras")
        novo["idiomas_documentos"] = list(dict.fromkeys(c for c in cods if c))
    return novo, sorted(set(erros))


def _de_rede(unidade: str) -> bool:
    return ctypes.windll.kernel32.GetDriveTypeW(unidade) == _UNIDADE_DE_REDE


def _rotulo_da_unidade(unidade: str) -> str:
    """Nome do volume com a letra; unidade de rede mostra o compartilhamento.

    Unidade de rede não é consultada pelo volume, que trava com o servidor fora
    do ar; e o modo sem diálogo evita o "insira um disco" de leitor vazio.
    """
    letra = unidade.rstrip("\\")
    k32 = ctypes.windll.kernel32
    if _de_rede(unidade):
        remoto = config.para_unc(letra)
        return f"{remoto} ({letra})" if remoto != letra else letra
    nome = ctypes.create_unicode_buffer(261)
    k32.SetThreadErrorMode(_SEM_DIALOGO_DE_ERRO, None)
    if k32.GetVolumeInformationW(unidade, nome, 261, None, None, None, None, 0) and nome.value:
        return f"{nome.value} ({letra})"
    return letra


def _texto_de_pasta(caminho: str) -> str:
    # "G:" sozinho é a pasta corrente da unidade G, e não a raiz dela.
    if re.fullmatch(r"[A-Za-z]:", caminho):
        caminho += "\\"
    return os.path.abspath(caminho).replace("/", "\\").rstrip("\\").casefold()


def _unidade_na_nuvem(unidade: str) -> bool:
    """A guarda de nuvem da lista de unidades.

    A unidade de rede é comparada com as raízes sincronizadas só como texto,
    pela letra e pelo UNC: a guarda completa resolve a raiz com `realpath`, que
    abre a unidade e, com o servidor fora do ar, espera o tempo-limite do SMB.
    """
    if not _de_rede(unidade):
        return nuvem.em_pasta_sincronizada(Path(unidade))
    formas = {_texto_de_pasta(unidade), _texto_de_pasta(config.para_unc(unidade.rstrip("\\")))}
    for raiz in nuvem.raizes_sincronizadas():
        r = _texto_de_pasta(raiz) if raiz else ""
        if r and any(f == r or f.startswith(r + "\\") for f in formas):
            return True
    return False


def _pastas(caminho: str | None) -> dict:
    if not caminho:
        unidades = list(os.listdrives())
        return {"caminho": "", "pai": None, "pastas": unidades,
                "rotulos": {u: _rotulo_da_unidade(u) for u in unidades},
                "nuvem": [u for u in unidades if _unidade_na_nuvem(u)]}
    pleno = os.path.abspath(caminho)
    nomes = []
    with os.scandir(pleno) as itens:
        for item in itens:
            try:
                if not item.is_dir(follow_symlinks=False):
                    continue
                if item.stat(follow_symlinks=False).st_file_attributes & _OCULTA_OU_SISTEMA:
                    continue
            except OSError:
                continue
            nomes.append(item.name)
            if len(nomes) >= MAX_PASTAS:
                break
    pai = os.path.dirname(pleno)
    return {"caminho": pleno, "pai": pai if pai != pleno else "",
            "pastas": sorted(nomes, key=str.casefold)}


def criar_app(motor: Motor, token: str, origens_permitidas: set[str], *,
              relogio: Callable[[], float] = time.monotonic,
              pasta_web: Path = PASTA_WEB,
              raiz_packs: Path = pacote.RAIZ_PACKS) -> FastAPI:
    # Sem /docs, /redoc e /openapi.json: os dois primeiros buscam arquivos em
    # CDN (rede) e o terceiro publicaria o mapa da API local.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.batimento = batimento = Batimento(relogio)
    segredo = token.encode("utf-8")

    def token_ok(valor) -> bool:
        return isinstance(valor, str) and hmac.compare_digest(valor.encode("utf-8"), segredo)

    @app.exception_handler(_Recusa)
    async def _recusa(_request: Request, exc: _Recusa):
        return _erro(exc.status, exc.codigo)

    @app.middleware("http")
    async def portao(request: Request, call_next):
        permitidas = frozenset(origens_permitidas)
        hosts = {urlsplit(o).netloc.lower() for o in permitidas}
        caminho = request.url.path
        origem = request.headers.get("origin")
        try:
            tamanho = int(request.headers.get("content-length") or 0)
        except ValueError:
            tamanho = TETO_CORPO + 1
        if (request.headers.get("host") or "").lower() not in hosts:
            resposta = _erro(403, "host")
        elif origem is not None and origem not in permitidas:
            resposta = _erro(403, "origem")
        elif tamanho > TETO_CORPO:
            resposta = _erro(413, "corpo_grande")
        elif (caminho.startswith("/api/") and caminho != "/api/batimento"
              and not token_ok(request.headers.get(CABECALHO_TOKEN))):
            resposta = _erro(403, "token")
        else:
            # O batimento confere o token na própria rota: só pedido que já
            # passou por todos os portões renova a espera inicial.
            if caminho != "/api/batimento":
                batimento.visto()
            resposta = await call_next(request)
        resposta.headers["Content-Security-Policy"] = CSP
        resposta.headers["X-Content-Type-Options"] = "nosniff"
        resposta.headers["Referrer-Policy"] = "no-referrer"
        resposta.headers["Cache-Control"] = "no-store"
        resposta.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        resposta.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        return resposta

    def estado() -> dict:
        e = dict(motor.estado())
        if "configurado" not in e:
            cfg = motor.config()
            e["configurado"] = bool(cfg.get("origens")) and bool(cfg.get("destinos"))
        return e

    @app.get("/api/estado")
    async def rota_estado():
        return await run_in_threadpool(estado)

    @app.post("/api/start")
    async def rota_start():
        await run_in_threadpool(motor.start)
        return await run_in_threadpool(estado)

    @app.post("/api/stop")
    async def rota_stop():
        await run_in_threadpool(motor.stop)
        return await run_in_threadpool(estado)

    @app.get("/api/config")
    async def rota_config():
        return await run_in_threadpool(motor.config)

    @app.post("/api/config")
    async def rota_salvar_config(request: Request):
        corpo = await _objeto(request)
        if corpo is None:
            raise _Recusa(400, "corpo_invalido")
        atual = await run_in_threadpool(motor.config)
        novo, erros = await run_in_threadpool(_mesclar, atual, corpo, raiz_packs)
        if not erros:
            erros = list(await run_in_threadpool(motor.salvar_config, novo))
        return {"erros": erros, "config": await run_in_threadpool(motor.config)}

    @app.post("/api/escolher-pasta")
    async def rota_escolher_pasta():
        # None: não há seletor nativo (modo navegador); "": a pessoa cancelou.
        escolha = await run_in_threadpool(motor.escolher_pasta)
        return {"caminho": escolha or None, "sem_seletor": escolha is None}

    @app.post("/api/conferir-pasta")
    async def rota_conferir_pasta(request: Request):
        corpo = await _objeto(request) or {}
        caminho, papel = corpo.get("caminho"), corpo.get("papel")
        if not isinstance(caminho, str) or not caminho.strip() or papel not in ("origens", "destinos"):
            raise _Recusa(400, "corpo_invalido")
        origens = _pastas_do_corpo(corpo.get("origens", []))
        destinos = _pastas_do_corpo(corpo.get("destinos", []))
        erros = await run_in_threadpool(motor.conferir_pasta, caminho, papel, origens, destinos)
        return {"erros": list(erros)}

    @app.post("/api/pastas")
    async def rota_pastas(request: Request):
        corpo = await _objeto(request) or {}
        caminho = corpo.get("caminho")
        if caminho is not None and not isinstance(caminho, str):
            raise _Recusa(400, "corpo_invalido")
        try:
            return await run_in_threadpool(_pastas, caminho)
        except OSError:
            raise _Recusa(409, "pasta_inacessivel")

    @app.post("/api/batimento")
    async def rota_batimento(request: Request):
        corpo = await _objeto(request) or {}
        if not (token_ok(request.headers.get(CABECALHO_TOKEN)) or token_ok(corpo.get("token"))):
            raise _Recusa(403, "token")
        if corpo.get("saindo") is True:
            batimento.saindo()
        else:
            batimento.pulso()
        return {"ok": True}

    @app.get("/api/idiomas")
    async def rota_idiomas():
        cfg = await run_in_threadpool(motor.config)
        idiomas = [{"codigo": c, "sigla": s, "nome": n, "rtl": r, "bandeira": bandeira(c),
                    "traduzido": (pasta_web / "i18n" / f"{c}.json").is_file(),
                    "regras": (raiz_packs / c).is_dir()}
                   for (c, s, n, r) in IDIOMAS]
        return {"idiomas": idiomas, "atual": canonico(cfg.get("idioma_interface")) or "pt-BR"}

    @app.get("/api/log")
    async def rota_log():
        return {"linhas": list(await run_in_threadpool(motor.log_recente))}

    @app.post("/api/salvar-log")
    async def rota_salvar_log(request: Request):
        corpo = await _objeto(request) or {}
        destino = corpo.get("destino")
        if not isinstance(destino, str) or not destino.strip():
            raise _Recusa(400, "destino_ausente")
        try:
            arquivo = await run_in_threadpool(motor.salvar_log, destino.strip())
        except OSError:
            raise _Recusa(409, "log_nao_gravado")
        return {"arquivo": arquivo}

    app.mount("/", StaticFiles(directory=pasta_web, html=True), name="web")
    return app
