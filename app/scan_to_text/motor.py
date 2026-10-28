"""O motor do app: configuração, vigia, ciclo e trabalhador atrás da tela.

No processo principal ficam a varredura e a fila. O arquivo cujo SHA-256 a
vigia já conhece e que todos os destinos já têm não sai da varredura; o resto
vai, um por vez, para o trabalhador, o processo filho que importa `tarefa`
daqui e roda `processar` (cópia, trava, leitura e escrita).

Arquivo que derruba o trabalhador (erro, tempo esgotado ou queda) volta na
varredura seguinte. Na vez `FALHAS_ATE_DESISTIR`, ele é entregue sem leitura,
em DESCONHECIDO com o original e o LEIA-ME: nem segura a fila, nem some. Se
nem isso der certo, fica de fora até o app abrir de novo. Só conta a falha
que é do arquivo: disco cheio, rede que cai, destino ou origem fora do ar e
leitor que não carrega (modelos, pacote de regras, bibliotecas ou memória na
carga, inclusive a do pipeline de cada formato, ou o filho que não importa a
tarefa) deixam o arquivo esperando na origem, sem contar. Memória ou
biblioteca que falta na leitura de um arquivo é falha dele: conta, e a rodada
segue para o próximo. A conta só zera quando o arquivo foi lido aqui ou já
está entregue: o destino ocupado, a trava de outra estação e a trava perdida
depois da entrega sem leitura não a mexem.

A estação também pode explicar a falha: o tempo esgotado com o filho sem CPU;
o tempo esgotado com a estação paginando, em que o filho, abaixo de um
núcleo, seguiu buscando páginas na prova com a estação sem RAM livre, e a
memória física disponível, somada à dele, fica abaixo do pico do leitor; e a
leitura que falhou de um jeito que a falta de memória explica, com a memória
livre da estação, medida depois de o filho sair, abaixo do pico do leitor. Aí
o arquivo espera sem contar, até `DESCULPAS_ATE_CONTAR` vezes; depois a falha
conta, para nenhum arquivo esperar para sempre. Cada espera manda o arquivo
para o fim da fila e encerra a rodada (`FalhaDesculpada`), sem aviso que
pausa: os de trás dariam com a
mesma estação, e cada um gastaria uma desculpa sua. A tela recebe um aviso
que não bloqueia até a próxima leitura bem-sucedida, até a varredura não
trazer nenhum arquivo que ainda vá ao trabalhador ou até o próximo START.

O arquivo em que o leitor não carregou vai para o fim da fila: se a falha é
só do formato dele, os arquivos de trás não ficam presos atrás dele.

As pastas só são validadas quando mudam: uma origem fora do ar não pode
impedir que se grave o intervalo do ping. A tela confere cada pasta na hora em
que ela é adicionada (`conferir_pasta`).

O estado leva a conta do dia (arquivos, documentos, documentos sem paciente,
arquivos ilegíveis ou entregues sem leitura e a hora do último) e as pastas
por trás de cada aviso de pasta. A conta fica em `<dados>/hoje.json`, para o
reinício do app não a zerar, e recomeça quando o dia muda.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import threading
import time
from datetime import date
from importlib import metadata
from pathlib import Path

from scan_to_text import __version__, config, log, modelos, nuvem, pacote, rede, registro
from scan_to_text.ciclo import (Ciclo, FalhaDesculpada, LeitorIndisponivel, disco_cheio,
                                falha_de_ambiente)
from scan_to_text.es import ES, OrigemIntocavel
from scan_to_text.escrever import atomico
from scan_to_text.log import evento
from scan_to_text.processar import Contexto, processar
from scan_to_text.trabalhador import (ErroNaTarefa, TempoEsgotado, Trabalhador,
                                      TrabalhadorCaiu, TrabalhadorParou, estado_da_memoria)
from scan_to_text.vigia import Vigia

VERSAO = __version__
ALVO = "scan_to_text.motor:tarefa"
FALHAS_ATE_DESISTIR = 2
# Tempo-limite por arquivo. A página leva até 30 s na estação mais fraca que
# ainda fica com o Docling, e a conta usa a página mais leve que o scanner
# entrega (cerca de 50 KB, preto e branco comprimido): é ela que põe mais
# páginas em cada MB. A base cobre o arranque do filho e a carga dos modelos,
# que correm dentro do mesmo prazo. O teto só existe para o filho que travou.
SEGUNDOS_POR_PAGINA = 30
BYTES_POR_PAGINA = 50 * 1024
TEMPO_BASE_S = 20 * 60
TEMPO_MAXIMO_S = 8 * 3600
LINHAS_DE_LOG = 200
LOGS = ("scan_to_text.log", "trabalhador.log")
PASTA_ESTACOES = ("_scan-to-text", "estacoes")
HOJE = "hoje.json"
_CONTAS = ("arquivos", "documentos", "desconhecidos", "ilegiveis")
# Avisos do preflight com que a estação não lê: a abertura não liga o motor.
PREFLIGHT_BLOQUEIA = ("aviso.vcpp_ausente", "aviso.windows_nao_suportado")
# O filho que não carregou o leitor ou ficou sem memória sai; a próxima
# tentativa começa num processo novo.
ESPERA_FILHO_S = 2
# Falhas da leitura que a falta de memória explica. O Docling captura o
# MemoryError e o `bad allocation` do ONNX Runtime dentro de cada estágio e
# devolve ConversionError, ou descarta a página e devolve as outras
# (LeituraIncompleta, do layout). O filho que cai sem responder pode ter sido
# o access violation na carga dos pesos. Pelo nome do tipo: o principal não
# importa o Docling nem o layout.
_FALHAS_DE_MEMORIA = ("MemoryError", "ConversionError", "LeituraIncompleta")
# Pico da memória comprometida do filho lendo, com os pipelines da imagem e
# do PDF com texto montados: medido no Job Object do filho, perto de 3,5 GB
# num scan A4 de 300 dpi (2,8 GB só com o da imagem). Com menos que isto
# livre, e o filho já fora, a leitura não cabe na estação.
MEMORIA_DO_LEITOR = 3_600 * 2**20
# Falhas que a estação explica e não contam, por arquivo; a seguinte conta.
DESCULPAS_ATE_CONTAR = 3
AVISO_ESTACAO_OCUPADA = "aviso.estacao_ocupada"
AVISO_MEMORIA_BAIXA = "aviso.memoria_baixa"

_CABECA = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),\d{3} \S+ \S+ \d+ (.*)$")
# ERROR_BAD_NETPATH, ERROR_UNEXP_NET_ERR, ERROR_NETNAME_DELETED e ERROR_BAD_NET_NAME.
_REDE_WIN = (53, 59, 64, 67)


class DestinoInacessivel(RuntimeError):
    """Um destino não existe como pasta: a tarefa é recusada sem criar nada nele."""


class MotorApp:
    def __init__(self, caminho_config: Path, dados: Path, *, estacao: str,
                 preflight: dict | None = None, trabalhador=None, escolher=None,
                 em_nuvem=nuvem.em_pasta_sincronizada, relogio=time.monotonic):
        self._caminho_config = Path(caminho_config)
        self._dados = Path(dados)
        self._estacao = estacao
        self._preflight = preflight or {}
        self._escolher = escolher or (lambda: None)
        self._em_nuvem = em_nuvem
        self._relogio = relogio
        self._ilegivel = False
        try:
            self._cfg = config.ler(self._caminho_config)
        except config.ConfigInvalida:
            # Não é gravado por cima: na primeira gravação o arquivo quebrado
            # é guardado ao lado, com o que a pessoa tinha configurado.
            self._cfg = copy.deepcopy(config.PADRAO)
            self._ilegivel = True
            evento("config_ilegivel")
        self._trabalhador = trabalhador or Trabalhador(ALVO, self._cfg["ocioso_min"])
        self._trava = threading.RLock()
        self._ciclo: Ciclo | None = None
        self._vigia: Vigia | None = None
        self._ativa: dict | None = None
        self._falhas: dict[tuple[str, int, int], int] = {}
        self._desculpas: dict[tuple[str, int, int], int] = {}  # falhas que a estação explicou
        self._avisos_da_estacao: set[str] = set()  # até ler, a fila esvaziar ou o START
        self._adiados: dict[str, None] = {}  # na ordem em que o leitor não carregou neles
        self._fechado = False
        # Configuração nova à espera de que o ping antigo saia: até lá o ciclo
        # antigo fica no lugar, e o START o encontra e recusa.
        self._remontar = False
        self._paradas = 0  # STOPs pedidos: um STOP no meio da troca vence o religamento
        self._dia = self._ler_dia()

    # -- o que a tela chama ----------------------------------------------------

    def estado(self) -> dict:
        with self._trava:
            ciclo, vigia, ativa = self._ciclo, self._vigia, self._ativa
            if self._dia["dia"] != date.today().isoformat():
                self._dia = self._dia_zerado()
            hoje = {k: v for k, v in self._dia.items() if k != "dia"}
            da_estacao = sorted(self._avisos_da_estacao)
        estado = ciclo.estado if ciclo is not None else "parado"
        avisos = list(ciclo.avisos) if ciclo is not None else []
        pastas = {}
        if estado != "parado" and vigia is not None and vigia.inacessiveis:
            avisos.append("origem_inacessivel")
            pastas["origem_inacessivel"] = [str(p) for p in vigia.inacessiveis]
        if "destino_inacessivel" in avisos and ativa is not None:
            pastas["destino_inacessivel"] = [d for d in ativa["destinos"] if not os.path.isdir(d)]
        if estado != "parado":
            avisos += da_estacao
        if self._ilegivel:
            avisos.append("aviso.config_ilegivel")
        preflight = list(self._preflight.get("avisos") or [])
        if AVISO_MEMORIA_BAIXA in avisos:
            # A memória medida na leitura é a de agora; a do preflight, da abertura.
            preflight = [a for a in preflight if a != "aviso.ram_baixa"]
        avisos += preflight
        return {"estado": estado, "avisos": avisos, "pastas": pastas, "hoje": hoje,
                "versao": VERSAO}

    def start(self) -> None:
        with self._trava:
            if self._fechado or not self._configurado():
                return
            if self._ciclo is not None and self._ciclo.estado != "parado":
                return
            if not self._preparar():
                return
            self._avisos_da_estacao.clear()  # são da vez anterior
            ciclo = self._ciclo
            ciclo.limpar_no_inicio()
            ciclo.start()
        evento("start")

    def stop(self) -> None:
        with self._trava:
            self._paradas += 1
            ciclo = self._ciclo
        if ciclo is not None:
            ciclo.stop()
        evento("stop")

    def config(self) -> dict:
        with self._trava:
            return copy.deepcopy(self._cfg)

    def salvar_config(self, cfg: dict) -> list[str]:
        novo = json.loads(json.dumps(cfg))
        with self._trava:
            atual = self._cfg
            pastas_mudaram = any(novo.get(k) != atual[k] for k in ("origens", "destinos"))
            if pastas_mudaram:
                erros = config.validar(novo, self._legivel(atual),
                                       self._gravavel(atual, novo.get("origens") or []),
                                       self._em_nuvem)
                if erros:
                    evento("config_recusada", erros=",".join(erros))
                    return erros
            if self._ilegivel and self._caminho_config.is_file():
                os.replace(self._caminho_config,
                           self._caminho_config.with_name(self._caminho_config.name + ".ilegivel"))
            config.gravar(self._caminho_config, novo)
            self._cfg = config.ler(self._caminho_config)
            self._ilegivel = False
            mudou_o_motor = any(self._cfg[k] != atual[k] for k in config.PADRAO
                                if k not in ("idioma_interface", "versao_config"))
            antigo = self._ciclo
            # "parando" não é ligado: a pessoa já mandou parar.
            rodando = antigo is not None and antigo.estado in ("ocioso", "trabalhando")
            if mudou_o_motor and antigo is not None:
                self._remontar = True  # o próximo START monta tudo com a configuração nova
            paradas = self._paradas
        evento("config_salva", origens=len(self._cfg["origens"]),
               destinos=len(self._cfg["destinos"]))
        if mudou_o_motor and rodando:
            antigo.stop()
            with self._trava:
                religar = self._paradas == paradas
            if religar:
                self.start()
        return []

    def escolher_pasta(self) -> str | None:
        return self._escolher()

    def conferir_pasta(self, caminho: str, papel: str, origens: list[str],
                       destinos: list[str]) -> list[str]:
        """Os erros que a pasta nova daria ao salvar, conferidos só para ela.

        A pasta que já está na lista do mesmo papel volta só com "pasta_repetida".
        """
        ja_listadas = {"origens": origens, "destinos": destinos}.get(papel) or []
        if any(config.mesma_pasta(caminho, p) for p in ja_listadas):
            return ["pasta_repetida"]
        nova = config.para_unc(caminho.strip())
        cfg = {"origens": list(origens) + ([nova] if papel == "origens" else []),
               "destinos": list(destinos) + ([nova] if papel == "destinos" else [])}
        alvo = Path(nova)
        return config.validar(cfg, lambda p: p != alvo or _pode_ler(p),
                              lambda p: p != alvo or _pode_gravar(p, cfg["origens"]),
                              lambda p: p == alvo and self._em_nuvem(p))

    def log_recente(self) -> list[str]:
        entradas = []
        for nome in LOGS:
            for linha in _cauda(self._dados / "logs" / nome, LINHAS_DE_LOG * 4):
                achado = _CABECA.match(linha)
                if achado:
                    entradas.append(f"{achado.group(1)} {achado.group(2)}  {achado.group(3)}")
        entradas.sort(key=lambda e: e[:19])
        return entradas[-LINHAS_DE_LOG:]

    def salvar_log(self, destino: str) -> str:
        pasta = Path(destino)
        try:
            ES([Path(o) for o in self.config()["origens"]]).exigir_fora_das_origens(pasta)
        except OrigemIntocavel:
            raise PermissionError("destino_em_origem") from None
        partes = [json.dumps({"versao": VERSAO, "estacao": self._estacao,
                              "preflight": self._preflight}, ensure_ascii=False, indent=2)]
        for nome in LOGS:
            base = self._dados / "logs" / nome
            for arquivo in [base.with_name(f"{nome}.{n}") for n in range(log.COPIAS, 0, -1)] + [base]:
                if arquivo.is_file():
                    partes.append(f"===== {arquivo.name} =====\n"
                                  + arquivo.read_text(encoding="utf-8", errors="replace"))
        alvo = pasta / f"Scan-to-Text-log-{_seguro(self._estacao)}-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        fd, parcial = tempfile.mkstemp(prefix=".log-", suffix=".parcial", dir=pasta)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\r\n") as saida:
                saida.write("\n\n".join(partes) + "\n")
            os.replace(parcial, alvo)
        finally:
            if os.path.exists(parcial):
                os.remove(parcial)
        evento("log_salvo")
        return str(alvo)

    # -- o que o app chama -----------------------------------------------------

    def abrir(self) -> None:
        """Na abertura do app: limpa o que esta estação deixou e, configurado, já liga.

        Com aviso de preflight que impede a leitura, não liga sozinho.
        """
        self.limpar()
        if any(a in PREFLIGHT_BLOQUEIA for a in self._preflight.get("avisos") or []):
            evento("start_automatico_recusado")
            return
        self.start()

    def limpar(self) -> None:
        """Na abertura: solta as travas órfãs desta estação, apaga os `.parcial` e a temporária."""
        with self._trava:
            if self._configurado() and self._preparar():
                self._ciclo.limpar_no_inicio()

    def fechar(self, espera_s: float | None = None) -> None:
        """Sequência de fechamento; `espera_s` encurta o prazo do STOP."""
        with self._trava:
            self._fechado = True
            ciclo = self._ciclo
        if ciclo is not None:
            ciclo.fechar(espera_s)
        self._trabalhador.parar(espera_s=0)
        evento("app_fechado")

    # -- por dentro --------------------------------------------------------------

    def _configurado(self) -> bool:
        return bool(self._cfg["origens"]) and bool(self._cfg["destinos"])

    def _preparar(self) -> bool:
        """Monta vigia e ciclo da configuração atual, se ainda não estão montados."""
        if self._ciclo is not None:
            if not self._remontar or self._ciclo.estado != "parado":
                return True
            self._ciclo = None  # o ping antigo saiu: só agora a configuração nova entra
        self._remontar = False
        cfg = copy.deepcopy(self._cfg)
        origens = [Path(o) for o in cfg["origens"]]
        es = ES(origens)
        self._trabalhador.ocioso_s = cfg["ocioso_min"] * 60
        vigia = Vigia(origens, cfg["estabilidade_s"], cfg["days_back"], relogio=self._relogio)
        try:
            ciclo = Ciclo(self._varrer, self._processar, cfg["ping_min"],
                          trabalhador=self._trabalhador,
                          destinos=[Path(d) for d in cfg["destinos"]], estacao=self._estacao,
                          pasta_temp=self._dados / "temp", es=es)
        except (ValueError, OrigemIntocavel) as erro:
            evento("temporaria_recusada", motivo=erro.args[0] if erro.args else "")
            return False
        self._ativa, self._vigia, self._ciclo = cfg, vigia, ciclo
        return True

    def _varrer(self) -> list[Path]:
        vigia, cfg = self._vigia, self._ativa
        destinos = [Path(d) for d in cfg["destinos"]]
        pendentes = []
        for caminho in vigia.varrer():
            sha = vigia.sha256_conhecido(caminho)
            if sha is not None and all(registro.ja_feito(d, sha) for d in destinos):
                continue
            pendentes.append(caminho)
        if not any(self._vai_ao_trabalhador(p) for p in pendentes):
            with self._trava:
                self._avisos_da_estacao.clear()  # nenhum arquivo espera mais
        self._bater_ponto(destinos)
        na_fila = {str(p) for p in pendentes}
        for chave in [c for c in self._adiados if c not in na_fila]:
            del self._adiados[chave]
        ordem = {chave: vez for vez, chave in enumerate(self._adiados, 1)}
        pendentes.sort(key=lambda p: ordem.get(str(p), 0))  # estável: o resto segue a vigia
        return pendentes

    def _vai_ao_trabalhador(self, caminho: Path) -> bool:
        """O `_processar` ainda manda o arquivo: existe e não ficou de fora até o app abrir."""
        try:
            st = os.stat(caminho)
        except OSError:
            return False
        return self._falhas.get(_chave(caminho, st), 0) <= FALHAS_ATE_DESISTIR

    def _processar(self, caminho: Path) -> None:
        try:
            st = os.stat(caminho)
        except OSError:
            return  # sumiu entre a varredura e agora
        chave = _chave(caminho, st)
        falhas = self._falhas.get(chave, 0)
        if falhas > FALHAS_ATE_DESISTIR:
            return
        cfg = self._ativa
        tarefa = {"arquivo": str(caminho), "origens": cfg["origens"], "destinos": cfg["destinos"],
                  "estacao": self._estacao, "idioma": cfg["idiomas_documentos"][0],
                  "hoje": date.today().isoformat(), "pasta_temp": str(self._dados / "temp"),
                  "pasta_logs": str(self._dados / "logs"),
                  "desistir": falhas >= FALHAS_ATE_DESISTIR}
        limite = min(TEMPO_MAXIMO_S,
                     TEMPO_BASE_S + SEGUNDOS_POR_PAGINA * st.st_size / BYTES_POR_PAGINA)
        try:
            resumo = self._trabalhador.enviar(tarefa, tempo_limite_s=limite)
        except TrabalhadorParou:
            raise
        except (ErroNaTarefa, TrabalhadorCaiu) as erro:
            if isinstance(erro, TempoEsgotado):
                _registrar_tempo_esgotado(erro)
            memoria = not falha_de_ambiente(erro) and _memoria_explica(erro)
            if falha_de_ambiente(erro) or memoria:
                # O filho pode ter ficado pela metade: a próxima tentativa
                # começa num processo novo, com a memória dele devolvida.
                self._trabalhador.parar(espera_s=ESPERA_FILHO_S)
            sem_cpu = isinstance(erro, TempoEsgotado) and erro.sem_cpu
            sem_memoria = (memoria and self._estacao_sem_memoria()) or _paginando(erro)
            with self._trava:
                if sem_cpu:
                    self._avisos_da_estacao.add(AVISO_ESTACAO_OCUPADA)
                if sem_memoria:
                    self._avisos_da_estacao.add(AVISO_MEMORIA_BAIXA)
            if _culpa_do_arquivo(erro, caminho, cfg["destinos"]):
                desculpas = self._desculpas.get(chave, 0)
                if (sem_cpu or sem_memoria) and desculpas < DESCULPAS_ATE_CONTAR:
                    self._desculpas[chave] = desculpas + 1
                    evento("arquivo_esperou", motivo="cpu" if sem_cpu else "memoria",
                           vez=desculpas + 1)
                    # Os de trás dariam com a mesma estação: a rodada para, e
                    # este volta depois deles.
                    self._adiados.pop(str(caminho), None)
                    self._adiados[str(caminho)] = None
                    raise FalhaDesculpada("falha_desculpada") from erro
                self._falhas[chave] = falhas + 1
                evento("arquivo_falhou", vez=falhas + 1)
            if falha_de_ambiente(erro):
                self._adiados.pop(str(caminho), None)
                self._adiados[str(caminho)] = None
            raise
        self._adiados.pop(str(caminho), None)
        situacao = resumo.get("situacao") if isinstance(resumo, dict) else None
        if situacao in ("feito", "trava_perdida") and not tarefa["desistir"]:
            with self._trava:
                self._avisos_da_estacao.clear()  # a estação voltou a ler
        if situacao in ("feito", "ja_feito") or (situacao == "trava_perdida"
                                                 and not tarefa["desistir"]):
            # Lido aqui sem falha, ou já entregue. O ocupado e a trava de outra
            # estação não dizem nada do arquivo: zerada por eles, a conta
            # deixaria o arquivo que falha voltar para sempre sem entrega.
            self._falhas.pop(chave, None)
            self._desculpas.pop(chave, None)
        if isinstance(resumo, dict) and resumo.get("sha256"):
            self._vigia.lembrar(caminho, resumo["sha256"])
        if isinstance(resumo, dict) and resumo.get("situacao") == "feito":
            self._contar(resumo)

    def _estacao_sem_memoria(self) -> bool:
        """A memória livre da estação, com o filho já fora, não cabe o leitor."""
        livre = commit_livre()
        if livre is None:
            return False
        evento("memoria_da_estacao", livre_mb=livre // 2**20,
               leitor_mb=MEMORIA_DO_LEITOR // 2**20)
        return livre < MEMORIA_DO_LEITOR

    @staticmethod
    def _dia_zerado() -> dict:
        return {"dia": date.today().isoformat(), **{c: 0 for c in _CONTAS}, "ultimo": None}

    def _ler_dia(self) -> dict:
        """A conta gravada, se é de hoje; senão, zerada."""
        dia = self._dia_zerado()
        try:
            gravado = json.loads((self._dados / HOJE).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return dia
        if not isinstance(gravado, dict) or gravado.get("dia") != dia["dia"]:
            return dia
        for chave in _CONTAS:
            valor = gravado.get(chave)
            if isinstance(valor, int) and not isinstance(valor, bool) and valor >= 0:
                dia[chave] = valor
        ultimo = gravado.get("ultimo")
        if isinstance(ultimo, str) and re.fullmatch(r"\d{2}:\d{2}", ultimo):
            dia["ultimo"] = ultimo
        return dia

    def _contar(self, resumo: dict) -> None:
        with self._trava:
            if self._dia["dia"] != date.today().isoformat():
                self._dia = self._dia_zerado()
            self._dia["arquivos"] += 1
            for chave in _CONTAS[1:]:
                self._dia[chave] += int(resumo.get(chave) or 0)
            self._dia["ultimo"] = time.strftime("%H:%M")
            dia = dict(self._dia)
        try:
            config.gravar(self._dados / HOJE, dia)
        except OSError:
            evento("hoje_nao_gravado")

    def _bater_ponto(self, destinos: list[Path]) -> None:
        """`<DESTINO>/_scan-to-text/estacoes/<ESTACAO>.json`: quem trabalha naquele destino.

        Destino que não existe como pasta fica sem ponto, e a raiz dele nunca
        é criada aqui: a letra ou o compartilhamento pode ter passado a apontar
        para outro lugar.
        """
        dados = json.dumps({"estacao": self._estacao, "versao": VERSAO, "pid": os.getpid(),
                            "batimento": round(time.time())}).encode("utf-8")
        es = ES([Path(o) for o in self._ativa["origens"]])
        for destino in destinos:
            alvo = destino.joinpath(*PASTA_ESTACOES, f"{_seguro(self._estacao)}.json")
            try:
                if not destino.is_dir():
                    continue  # destino fora do ar já aparece como aviso do ciclo
                pasta = destino
                for parte in PASTA_ESTACOES:
                    pasta = pasta / parte
                    pasta.mkdir(exist_ok=True)  # sem `parents`: some o destino, falha aqui
                atomico.gravar(alvo, dados, es, self._estacao, os.getpid())
            except (OSError, OrigemIntocavel):
                pass

    def _legivel(self, atual: dict):
        aceitas = set(atual["origens"])
        return lambda pasta: str(pasta) in aceitas or _pode_ler(pasta)

    def _gravavel(self, atual: dict, origens: list[str]):
        aceitos = set(atual["destinos"])
        return lambda pasta: str(pasta) in aceitos or _pode_gravar(pasta, origens)


def _chave(caminho: Path, st: os.stat_result) -> tuple[str, int, int]:
    """A conta das falhas e das desculpas: o arquivo que muda começa do zero."""
    return (str(caminho), st.st_size, st.st_mtime_ns)


def _culpa_do_arquivo(erro: BaseException, caminho: Path, destinos: list[str]) -> bool:
    """A falha conta contra o arquivo só quando o ambiente e as pastas estavam bem.

    Disco cheio, rede que cai, destino ou origem fora do ar e leitor que não
    carrega não são defeito do arquivo: contados, eles o levariam a
    DESCONHECIDO sem leitura. A falta de CPU e de memória da estação é
    descontada à parte, com teto, em `MotorApp._processar`.
    """
    if disco_cheio(erro) or falha_de_ambiente(erro):
        return False
    if getattr(erro, "winerror", None) in _REDE_WIN:
        return False
    if isinstance(erro, ErroNaTarefa) and erro.tipo == DestinoInacessivel.__name__:
        return False
    if not all(os.path.isdir(d) for d in destinos):
        return False
    try:
        os.stat(caminho)
    except OSError:
        return False
    return True


def _memoria_explica(erro: BaseException) -> bool:
    if isinstance(erro, ErroNaTarefa):
        return erro.tipo in _FALHAS_DE_MEMORIA
    return isinstance(erro, TrabalhadorCaiu) and not isinstance(erro, TempoEsgotado)


def _paginando(erro: BaseException) -> bool:
    """O estouro com a estação paginando: o filho lento seguiu buscando páginas
    com a estação sem RAM livre, e a leitura não cabe na memória física, nem
    contada a dele.

    É a RAM física cheia com o commit ainda livre: `commit_livre` não a vê.
    """
    return (isinstance(erro, TempoEsgotado) and erro.esperou_paginas
            and erro.fisica_disponivel is not None
            and erro.fisica_disponivel < MEMORIA_DO_LEITOR)


def _registrar_tempo_esgotado(erro: TempoEsgotado) -> None:
    prova = None if erro.cpu_na_prova is None else round(100 * erro.cpu_na_prova)
    if erro.relogio_s is None or erro.cpu_s is None or erro.folga is None:
        evento("tempo_esgotado", medida="ausente", quer_cpu=erro.quer_cpu)
        return
    paginas = erro.falhas_de_pagina_por_s
    livre, fisica = erro.fisica_livre, erro.fisica_disponivel
    evento("tempo_esgotado", segundos=round(erro.relogio_s), cpu_s=round(erro.cpu_s),
           folga_pct=round(100 * erro.folga), quer_cpu=erro.quer_cpu, prova_pct=prova,
           sem_cpu=erro.sem_cpu, paginas_s=None if paginas is None else round(paginas),
           livre_mb=None if livre is None else livre // 2**20,
           fisica_mb=None if fisica is None else fisica // 2**20)


def commit_livre() -> int | None:
    """Memória que a estação ainda pode comprometer (RAM e paginação), em bytes.

    É dela que sai cada alocação: quando acaba, vem o MemoryError e o
    `bad allocation`, mesmo com RAM física livre. None sem leitura.
    """
    estado = estado_da_memoria()
    return None if estado is None else estado.ullAvailPageFile


def _pode_ler(pasta: Path) -> bool:
    try:
        with os.scandir(pasta) as itens:
            next(itens, None)
        return True
    except OSError:
        return False


def _pode_gravar(pasta: Path, origens: list[str]) -> bool:
    """Cria e apaga um arquivo de teste; numa origem, recusa sem criar nada."""
    try:
        if ES([Path(o) for o in origens]).dentro_de_origem(Path(pasta)):
            return False
        fd, teste = tempfile.mkstemp(prefix=".scan-to-text-teste-", dir=pasta)
        os.close(fd)
        os.remove(teste)
        return True
    except (OSError, ValueError):  # ValueError: caminho que o Windows não aceita
        return False


def _cauda(arquivo: Path, linhas: int) -> list[str]:
    try:
        with open(arquivo, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - linhas * 400))
            return f.read().decode("utf-8", errors="replace").splitlines()[-linhas:]
    except OSError:
        return []


def _seguro(texto: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", texto)[:40] or "estacao"


# ---------------------------------------------------------------------------
# O lado do trabalhador
# ---------------------------------------------------------------------------

# O que o processo filho guarda entre uma tarefa e outra: o leitor com os
# modelos carregados, os pacotes de regras e as versões.
_PROCESSO: dict = {}


class _LeitorPreguicoso:
    """Carrega os modelos só na primeira leitura: arquivo já feito não paga a carga.

    `preparar` é chamado por `processar` antes de cronometrar a leitura: o
    tempo por página do log é o da leitura, e a carga sai num evento próprio.
    """

    def __init__(self):
        self._leitor = None

    def preparar(self) -> None:
        if self._leitor is None:
            inicio = time.perf_counter()
            try:
                from scan_to_text.ler.layout import Leitor
                self._leitor = Leitor(modelos.pasta_modelos())
            except Exception as erro:
                # DLL que falta, modelo que não abre, memória que não basta:
                # nenhum arquivo seria lido, e a culpa não é de nenhum deles.
                raise LeitorIndisponivel("leitor_indisponivel") from erro
            evento("modelos_carregados", segundos=round(time.perf_counter() - inicio, 1))

    def ler_arquivo(self, caminho: Path, sha256: str):
        self.preparar()
        from scan_to_text.ler.layout import FormatoIndisponivel
        try:
            return self._leitor.ler_arquivo(caminho, sha256)
        except FormatoIndisponivel as erro:
            # O pipeline do formato, montado na primeira leitura dele, é carga
            # do leitor: nenhum arquivo daquele formato seria lido.
            causa = erro.__cause__
            evento("formato_indisponivel", formato=erro.formato.lower(),
                   causa=type(causa).__name__.lower(), winerror=getattr(causa, "winerror", None))
            raise LeitorIndisponivel("leitor_indisponivel") from erro


def _versoes() -> dict:
    versoes = {"app": VERSAO}
    for nome in ("docling", "rapidocr", "onnxruntime", "torch"):
        try:
            versoes[nome] = metadata.version(nome)
        except metadata.PackageNotFoundError:
            pass
    return versoes


def tarefa(dados: dict) -> dict:
    """Roda no trabalhador: processa um arquivo e devolve o resumo, sem dado de paciente."""
    try:
        if "leitor" not in _PROCESSO:
            rede.ligar()
            log.configurar(Path(dados["pasta_logs"]), arquivo="trabalhador.log")
            modelos.preparar_ambiente()
            _PROCESSO["leitor"] = _LeitorPreguicoso()
            _PROCESSO["versoes"] = _versoes()
            # Versão com "v" na frente: o filtro do log redige número solto com ponto.
            evento("trabalhador_inicio", **{k: f"v{v}" for k, v in _PROCESSO["versoes"].items()})
        pacotes = _PROCESSO.setdefault("pacotes", {})
        idioma = dados["idioma"]
        if idioma not in pacotes:
            pacotes[idioma] = pacote.carregar(idioma)
    except Exception as erro:
        raise LeitorIndisponivel("leitor_indisponivel") from erro
    if not all(os.path.isdir(d) for d in dados["destinos"]):
        # A trava e a escrita criariam o caminho inteiro de um destino que sumiu.
        raise DestinoInacessivel("destino_inacessivel")
    ctx = Contexto(pacote=pacotes[idioma], leitor=_PROCESSO["leitor"],
                   es=ES([Path(o) for o in dados["origens"]]),
                   destinos=[Path(d) for d in dados["destinos"]], estacao=dados["estacao"],
                   pid=os.getpid(), hoje=date.fromisoformat(dados["hoje"]),
                   pasta_temp=Path(dados["pasta_temp"]), versoes=_PROCESSO["versoes"])
    return processar(Path(dados["arquivo"]), ctx, desistir=bool(dados.get("desistir")))
