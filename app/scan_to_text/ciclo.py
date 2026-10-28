"""Ciclo de vida do motor: START, STOP, fechar e limpeza no início.

Estados: "parado", "ocioso", "trabalhando" e "parando".

- `start()` liga o ping: uma thread que varre agora e depois a cada
  `ping_min` minutos, e processa os arquivos um por vez, na ordem em que a
  varredura os devolve (o mais antigo primeiro).
- `stop()` para o ping e o trabalhador em até `ESPERA_STOP_S`. O arquivo
  interrompido não chega a ser marcado como feito e volta na próxima
  varredura; a gravação por `.parcial` impede que algo fique pela metade.
  Se `processar` não obedecer no prazo, o estado fica "parando" até ele
  voltar: o ciclo não finge que parou.
- `fechar()` é a sequência de fechamento: para o ping, sinaliza o trabalhador,
  espera no máximo o prazo do STOP (ou um mais curto, quando o Windows dá
  poucos segundos ao processo), libera as travas desta estação e deste
  processo e apaga os `.parcial` desta estação. Depois dele não há START.
  Se `processar` ainda estiver rodando, as travas deste processo e os
  `.parcial` ficam: são do arquivo em curso.
- `limpar_no_inicio()` solta as travas órfãs desta estação (PID que já não
  existe), apaga os `.parcial` dela e esvazia a pasta temporária. A pasta
  temporária exige o `es` e é recusada, na construção e de novo antes de
  esvaziar, se for junção ou link, se ficar na raiz ou dentro de uma origem,
  ou se contiver um destino, o perfil, os dados de aplicativos, a temporária
  do sistema ou o próprio app.

`avisos` traz os códigos que a tela mostra enquanto o ping roda:
- "destino_inacessivel": um destino não responde como pasta. A rodada não
  processa nada (a conferência se repete antes de cada arquivo), e os
  arquivos esperam na origem até o próximo ping;
- "disco_cheio": um arquivo falhou por falta de espaço. A rodada para ali,
  porque os seguintes falhariam igual, e o próximo ping tenta de novo. O
  aviso sai quando um arquivo termina ou quando a varredura não traz nenhum;
- "leitor_indisponivel": o leitor não carregou (modelos, pacote de regras,
  bibliotecas ou memória que não bastam na carga) ou o trabalhador não
  importou a tarefa. Pausa a rodada como o disco cheio, e sai do mesmo
  jeito. Memória ou biblioteca que falta na leitura de um arquivo é do
  arquivo: não dá aviso, e a rodada segue.

A falha que a estação explica (`FalhaDesculpada`: sem CPU ou sem memória
para ler) também encerra a rodada, mas sem aviso que pausa: os arquivos de
trás dariam com a mesma estação, e o aviso dela já vem do motor.

Um destino inacessível nunca impede a limpeza dos outros, e nenhuma falha de
limpeza levanta: o fechamento tem de chegar ao fim.
"""
from __future__ import annotations

import errno
import logging
import os
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from scan_to_text.es import OrigemIntocavel, normalizar_caminho
from scan_to_text.escrever.arvore import limpar_sinais as _limpar_sinais
from scan_to_text.escrever.atomico import limpar_parciais as _limpar_parciais
from scan_to_text.log import evento
from scan_to_text.trabalhador import AlvoIndisponivel, ErroNaTarefa, quadros
from scan_to_text.travas import pid_vivo, soltar_orfas

if TYPE_CHECKING:
    from scan_to_text.es import ES
    from scan_to_text.trabalhador import Trabalhador

# Margem sob o teto de 10 s do STOP para o que vem depois do prazo (registro, retorno).
ESPERA_STOP_S = 9.0
_FATIA_S = 0.1

# ERROR_HANDLE_DISK_FULL e ERROR_DISK_FULL. O 39 chega com errno EINVAL, e não
# ENOSPC: só o winerror o reconhece.
_DISCO_CHEIO_WIN = (39, 112)

_log = logging.getLogger(__name__)


class Ciclo:
    def __init__(self, varrer: Callable[[], list[Path]], processar: Callable[[Path], None],
                 ping_min: float, ao_mudar: Callable[[str], None] | None = None, *,
                 trabalhador: Trabalhador | None = None, destinos: Sequence[Path] = (),
                 estacao: str = "", pasta_temp: Path | None = None, es: ES | None = None):
        self._varrer = varrer
        self._processar = processar
        self._ping_s = ping_min * 60
        self._ao_mudar = ao_mudar
        self._trabalhador = trabalhador
        self._destinos = [Path(d) for d in destinos]
        self._estacao = estacao
        self._es = es
        self._pasta_temp = None if pasta_temp is None else self._validar_temp(Path(pasta_temp))
        # Estado e aviso andam juntos sob a mesma trava: sem isso, o "parado"
        # da thread do ping pode chegar a quem escuta antes do "parando".
        self._mudanca = threading.RLock()
        self._estado = "parado"
        self._avisos: set[str] = set()
        self._fio: threading.Thread | None = None
        self._parar = threading.Event()
        self._fechado = False

    @property
    def estado(self) -> str:
        return self._estado

    @property
    def avisos(self) -> list[str]:
        with self._mudanca:
            return sorted(self._avisos)

    def start(self) -> None:
        with self._mudanca:
            if self._fechado or self._fio is not None:
                return  # já ligado, ainda parando, ou fechado
            self._parar = threading.Event()
            # Daemon: um `processar` que não obedece não pode segurar a saída
            # do processo depois do fechamento.
            self._fio = threading.Thread(target=self._ping, args=(self._parar,), daemon=True,
                                         name="scan_to_text_ping")
            self._mudar("ocioso")
            self._fio.start()

    def stop(self, espera_s: float | None = None) -> None:
        """Para em até `espera_s` (padrão: `ESPERA_STOP_S`)."""
        espera = ESPERA_STOP_S if espera_s is None else espera_s
        inicio = time.monotonic()
        with self._mudanca:
            fio = self._fio
            if fio is not None:
                self._parar.set()
                self._mudar("parando")
        trabalhador = self._trabalhador
        if trabalhador is not None and trabalhador.vivo:
            trabalhador.parar(espera_s=espera / 2)
        if fio is None:
            return
        while fio.is_alive():
            restante = inicio + espera - time.monotonic()
            if restante <= 0:
                break
            fio.join(min(restante, _FATIA_S))
            if fio.is_alive() and trabalhador is not None and trabalhador.vivo:
                # O ping pode ter mandado uma tarefa entre a checagem da parada
                # e o envio, fazendo nascer um filho novo depois do `parar`.
                trabalhador.parar(espera_s=0)
        if fio.is_alive():
            evento("ciclo_stop_sem_resposta", segundos=round(time.monotonic() - inicio, 1))
        else:
            evento("ciclo_parado", segundos=round(time.monotonic() - inicio, 1))

    def fechar(self, espera_s: float | None = None) -> None:
        with self._mudanca:
            self._fechado = True
        self.stop(espera_s)
        with self._mudanca:
            em_curso = self._fio is not None
        if em_curso:
            # `processar` não obedeceu ao STOP e ainda segura a trava e grava o
            # `.parcial` do arquivo em curso. Soltá-los deixaria outra estação
            # gravar o mesmo arquivo junto. Ficam para o próximo início desta
            # estação (PID morto) ou para a parada de 15 min das outras.
            evento("ciclo_fechado_com_arquivo_em_curso")
            self._limpar_destinos(pid_vivo, parciais=False)
        else:
            meu_pid = os.getpid()
            # As travas com o PID deste processo são deste processo: depois do
            # STOP, nenhuma delas tem mais quem a renove.
            self._limpar_destinos(lambda pid: pid != meu_pid and pid_vivo(pid))
        evento("ciclo_fechado")

    def limpar_no_inicio(self) -> None:
        with self._mudanca:
            if self._fio is not None:
                # Apagaria o `.parcial` e a cópia temporária do arquivo em curso.
                evento("ciclo_limpeza_com_ping_ligado")
                return
        self._limpar_destinos(pid_vivo)
        if self._pasta_temp is None:
            return
        try:
            # De novo, perto do esvaziamento: a pasta pode ter virado junção
            # depois da construção.
            self._validar_temp(self._pasta_temp)
        except (ValueError, OrigemIntocavel) as erro:
            motivo = erro.args[0] if isinstance(erro, ValueError) else "dentro_de_origem"
            evento("ciclo_temporaria_recusada", motivo=motivo)
            return
        try:
            apagados = self._esvaziar(self._pasta_temp)
        except OSError as erro:
            _registrar_erro("ciclo_erro_ao_limpar_temporarios", erro)
        else:
            evento("temporarios_apagados", quantidade=apagados)

    # -- o ping --------------------------------------------------------------

    def _ping(self, parar: threading.Event) -> None:
        try:
            while not parar.is_set():
                try:
                    arquivos = list(self._varrer())
                except Exception as erro:  # noqa: BLE001 - origem caída não derruba o ping
                    _registrar_erro("ciclo_erro_ao_varrer", erro)
                    arquivos = []
                if not arquivos:
                    # Nada espera: não há o que pausar.
                    self._retirar_aviso("disco_cheio")
                    self._retirar_aviso("leitor_indisponivel")
                if self._destino_fora():
                    # Os arquivos ficam na origem e voltam na próxima varredura.
                    self._dar_aviso("destino_inacessivel")
                    arquivos = []
                else:
                    self._retirar_aviso("destino_inacessivel")
                for caminho in arquivos:
                    if self._destino_fora():
                        # Conferido a cada arquivo: o destino pode cair no meio da rodada.
                        self._dar_aviso("destino_inacessivel")
                        break
                    with self._mudanca:
                        if parar.is_set():
                            break
                        self._mudar("trabalhando")
                    try:
                        self._processar(caminho)
                    except Exception as erro:  # noqa: BLE001 - um arquivo ruim não para a fila
                        if parar.is_set():
                            evento("ciclo_arquivo_interrompido")
                        elif disco_cheio(erro):
                            _registrar_erro("ciclo_disco_cheio", erro)
                            self._dar_aviso("disco_cheio")
                            break
                        elif falha_de_ambiente(erro):
                            _registrar_erro("ciclo_leitor_indisponivel", erro)
                            self._dar_aviso("leitor_indisponivel")
                            break
                        elif isinstance(erro, FalhaDesculpada):
                            _registrar_erro("ciclo_falha_desculpada", erro.__cause__ or erro)
                            break
                        elif self._destino_fora():
                            _registrar_erro("ciclo_destino_inacessivel", erro)
                            self._dar_aviso("destino_inacessivel")
                            break
                        else:
                            _registrar_erro("ciclo_erro_ao_processar", erro)
                    else:
                        self._retirar_aviso("disco_cheio")
                        self._retirar_aviso("leitor_indisponivel")
                with self._mudanca:
                    if not parar.is_set():
                        self._mudar("ocioso")
                parar.wait(self._ping_s)
        finally:
            with self._mudanca:
                self._fio = None
                self._avisos.clear()
                self._mudar("parado")

    def _mudar(self, estado: str) -> None:
        """Troca o estado e avisa. Só com `self._mudanca` segura."""
        if estado == self._estado:
            return
        self._estado = estado
        evento("ciclo_estado", estado=estado)
        if self._ao_mudar is not None:
            try:
                self._ao_mudar(estado)
            except Exception as erro:  # noqa: BLE001 - a tela caída não derruba o motor
                _registrar_erro("ciclo_erro_ao_avisar", erro)

    def _dar_aviso(self, codigo: str) -> None:
        with self._mudanca:
            if codigo in self._avisos:
                return
            self._avisos.add(codigo)
        evento("ciclo_aviso", aviso=codigo)

    def _retirar_aviso(self, codigo: str) -> None:
        with self._mudanca:
            if codigo not in self._avisos:
                return
            self._avisos.discard(codigo)
        evento("ciclo_aviso_retirado", aviso=codigo)

    def _destino_fora(self) -> bool:
        # Destino que sumiu não é recriado: a letra ou o compartilhamento pode
        # ter passado a apontar para outro lugar.
        return any(not os.path.isdir(destino) for destino in self._destinos)

    # -- limpeza -------------------------------------------------------------

    def _limpar_destinos(self, vivo: Callable[[int], bool], parciais: bool = True) -> None:
        for indice, destino in enumerate(self._destinos):
            try:
                soltas = soltar_orfas(destino, self._estacao, vivo)
            except Exception as erro:  # noqa: BLE001 - um destino não impede os outros
                _registrar_erro("ciclo_erro_ao_soltar_travas", erro)
            else:
                if soltas:
                    evento("travas_soltas", destino=indice, quantidade=soltas)
            if not parciais:
                continue
            try:
                _limpar_parciais(destino, self._estacao, es=self._es)
                _limpar_sinais(destino, self._estacao)
            except Exception as erro:  # noqa: BLE001 - um destino não impede os outros
                _registrar_erro("ciclo_erro_ao_apagar_parciais", erro)

    def _validar_temp(self, pasta: Path) -> Path:
        """Recusa a pasta temporária cujo esvaziamento apagaria o que não é dela.

        O texto do caminho não diz para onde ele leva: a comparação é pelo
        caminho resolvido (junção, link e letra mapeada) e pela identidade no
        disco.
        """
        if self._es is None:
            # Sem o módulo seguro, uma origem dentro da pasta seria apagada.
            raise ValueError("pasta_temp_sem_es")
        pasta = Path(os.path.abspath(pasta))
        if _e_ponto_de_reanalise(pasta):
            # Esvaziar uma junção apaga o conteúdo da pasta para onde ela aponta.
            raise ValueError("pasta_temp_e_link")
        real = Path(normalizar_caminho(pasta))
        if pasta.parent == pasta or real.parent == real:
            raise ValueError("pasta_temp_na_raiz")
        for destino in self._destinos:
            if _contem(pasta, destino):
                raise ValueError("pasta_temp_contem_destino")
        for alheia in _pastas_alheias():
            if _contem(pasta, alheia):
                raise ValueError("pasta_temp_contem_pasta_alheia")
        self._es.exigir_fora_das_origens(pasta)
        return pasta

    def _esvaziar(self, pasta: Path) -> int:
        """Apaga o conteúdo de `pasta` e a mantém; devolve quantos itens saíram.

        Junção e link saem sem que se entre neles: o que apontam fica fora da
        pasta temporária. Origem que esteja lá dentro não é tocada.
        """
        try:
            entradas = list(os.scandir(pasta))
        except FileNotFoundError:
            return 0
        apagados = 0
        for entrada in entradas:
            caminho = Path(entrada.path)
            try:
                if self._es.dentro_de_origem(caminho):
                    continue
                atributos = entrada.stat(follow_symlinks=False).st_file_attributes
                if entrada.is_junction() or entrada.is_symlink():
                    if atributos & stat.FILE_ATTRIBUTE_DIRECTORY:
                        os.rmdir(caminho)
                    else:
                        os.unlink(caminho)
                elif entrada.is_dir(follow_symlinks=False):
                    apagados += self._esvaziar(caminho)
                    if atributos & stat.FILE_ATTRIBUTE_READONLY:
                        os.chmod(caminho, stat.S_IWRITE)
                    os.rmdir(caminho)
                else:
                    if atributos & stat.FILE_ATTRIBUTE_READONLY:
                        os.chmod(caminho, stat.S_IWRITE)
                    os.remove(caminho)
                apagados += 1
            except OSError as erro:
                _registrar_erro("ciclo_erro_ao_apagar_temporario", erro)
        return apagados


class LeitorIndisponivel(RuntimeError):
    """O leitor não carrega: modelos, pacote de regras, bibliotecas ou memória que não bastam."""


class FalhaDesculpada(RuntimeError):
    """A estação explicou a falha do arquivo (sem CPU ou sem memória): ele espera sem contar.

    A causa é a falha que a estação explicou.
    """


def falha_de_ambiente(erro: BaseException) -> bool:
    """A falha é da estação, e não do arquivo: o arquivo espera, e não é contado.

    Só a carga do leitor e o filho que não importa a tarefa. O erro do
    trabalhador chega como `ErroNaTarefa`, com o nome do tipo.
    """
    if isinstance(erro, AlvoIndisponivel):
        return True
    if isinstance(erro, ErroNaTarefa):
        return erro.tipo == LeitorIndisponivel.__name__
    return isinstance(erro, LeitorIndisponivel)


def disco_cheio(erro: BaseException) -> bool:
    """Falta de espaço no erro ou em alguma causa dele.

    `ErroNaTarefa` traz `errno` e `winerror` do OSError que o trabalhador viu.
    """
    vistos: set[int] = set()
    atual: BaseException | None = erro
    while atual is not None and id(atual) not in vistos:
        vistos.add(id(atual))
        if (getattr(atual, "winerror", None) in _DISCO_CHEIO_WIN
                or getattr(atual, "errno", None) == errno.ENOSPC):
            return True
        atual = atual.__cause__ or atual.__context__
    return False


def _e_ponto_de_reanalise(caminho: Path) -> bool:
    try:
        atributos = os.lstat(caminho).st_file_attributes
    except FileNotFoundError:
        return False
    except OSError:
        return True  # sem como conferir, conta como link: a pasta é recusada
    return bool(atributos & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _pastas_alheias() -> list[Path]:
    """O que a pasta temporária nunca pode conter: o perfil, os dados de
    aplicativos, a temporária do sistema e o próprio app."""
    pastas = [Path.home(), Path(tempfile.gettempdir()), Path(__file__).resolve().parent]
    for variavel in ("USERPROFILE", "LOCALAPPDATA", "APPDATA"):
        valor = os.environ.get(variavel)
        if valor:
            pastas.append(Path(valor))
    return pastas


def _contem(pasta: Path, alvo: Path) -> bool:
    """`alvo` é `pasta` ou fica abaixo dela, pelo caminho resolvido ou pela identidade."""
    base = normalizar_caminho(pasta)
    texto = normalizar_caminho(alvo)
    if texto == base or texto.startswith(base.rstrip("\\") + "\\"):
        return True
    # Dois caminhos diferentes para a mesma pasta (outro nome do servidor,
    # `\\localhost\c$`): só a identidade os casa.
    identidade = _identidade(base)
    atual = texto
    while identidade is not None:
        if _identidade(atual) == identidade:
            return True
        acima = os.path.dirname(atual)
        if acima == atual:
            break
        atual = acima
    return False


def _identidade(caminho: str) -> tuple[int, int] | None:
    try:
        estado = os.stat(caminho)
    except (OSError, ValueError):
        return None
    return (estado.st_dev, estado.st_ino) if estado.st_ino else None


def _registrar_erro(codigo: str, erro: BaseException) -> None:
    """Registra o tipo e os quadros do rastro (arquivo, linha, função).

    Nunca a mensagem: ela pode trazer texto de documento ou nome de paciente.
    O texto entra na própria mensagem, e não como argumento, porque o filtro
    do log redige argumento com maiúscula, e nome de tipo e de arquivo de
    código não identificam ninguém.
    """
    linhas = [f"{codigo} tipo={type(erro).__name__}"]
    if isinstance(erro, ErroNaTarefa):
        linhas[0] += f" tipo_no_trabalhador={erro.tipo}"
        linhas.append(erro.rastro)
    linhas.append(quadros(erro))
    _log.warning("\n".join(linha for linha in linhas if linha))
