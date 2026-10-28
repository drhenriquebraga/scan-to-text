"""Servidor da API local da tela: 127.0.0.1, porta efêmera, numa thread.

O soquete é aberto aqui, e não pelo uvicorn, para a porta ser conhecida antes
de a tela abrir. `SO_EXCLUSIVEADDRUSE` impede que outro programa da máquina se
prenda à mesma porta e responda no lugar do app.
"""
import socket
import threading
import time

import uvicorn

ESPERA_S = 10.0


class ServidorNaoSubiu(RuntimeError):
    pass


class Servidor:
    def __init__(self, app):
        self._soquete = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._soquete.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        self._soquete.bind(("127.0.0.1", 0))
        self.porta = self._soquete.getsockname()[1]
        self._servidor = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False,
                                                       lifespan="off"))
        self._fio = threading.Thread(target=self._servidor.run,
                                     kwargs={"sockets": [self._soquete]}, daemon=True,
                                     name="scan_to_text_api")

    @property
    def origem(self) -> str:
        return f"http://127.0.0.1:{self.porta}"

    def iniciar(self) -> None:
        self._fio.start()
        limite = time.monotonic() + ESPERA_S
        while not self._servidor.started:
            if not self._fio.is_alive() or time.monotonic() > limite:
                raise ServidorNaoSubiu("api_nao_subiu")
            time.sleep(0.02)

    def parar(self) -> None:
        self._servidor.should_exit = True
        self._fio.join(ESPERA_S)
        self._soquete.close()
