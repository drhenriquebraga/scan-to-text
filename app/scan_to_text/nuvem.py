"""Guarda de nuvem: destino sincronizado com a nuvem é recusado.

Pasta de paciente dentro de uma pasta que o Google Drive ou o OneDrive
espelham sai do ambiente da clínica sem ninguém notar. A resposta vem da
configuração do próprio cliente de sincronização, e NUNCA do nome da pasta:
casar "google drive" no caminho deixa passar a raiz espelhada de nome
próprio, que é o arranjo que o Drive recomenda, e acusa uma pasta local que
por acaso se chame "sync".

Fontes, nesta ordem:
  1. `RADAI_RAIZES_NUVEM` (caminhos separados por `;`): o que a TI declara e
     não há como descobrir (agente de backup, NAS replicado);
  2. Google Drive: `%LOCALAPPDATA%\\Google\\DriveFS\\root_preference_sqlite.db`,
     tabela `roots`, coluna `last_seen_absolute_path`, lida por `sqlite3` em
     modo somente leitura; e `SyncTargets` em `HKCU\\Software\\Google\\
     DriveFS\\Share`, que traz também a letra da unidade virtual do Drive
     (`G:` por padrão), onde o banco não chega;
  3. OneDrive: as variáveis `OneDrive*` que o cliente define e as contas e
     bibliotecas que ele registra em `HKCU`.

Nunca levanta: roda na validação da tela, e um banco ilegível só tira aquela
fonte da conta.

Porte de `em_pasta_sincronizada` e `raizes_espelhadas` do local-suite
(`app/radai_archive.py`, commit 55cffa9, Apache-2.0), sem o casamento por
nome, com o OneDrive lido da configuração e junções resolvidas.
"""
import os
import re
import sqlite3
from pathlib import Path

VARIAVEL_RAIZES = "RADAI_RAIZES_NUVEM"
_DRIVE_DB = ("Google", "DriveFS", "root_preference_sqlite.db")
_DRIVE_REGISTRO = "Software\\Google\\DriveFS\\Share"
_PARECE_CAMINHO = re.compile(r"^([A-Za-z]:(\\.*)?|\\\\[^\\].*)$", re.S)
_ONEDRIVE_VARIAVEIS = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")
_ONEDRIVE_CONTAS = "Software\\Microsoft\\OneDrive\\Accounts"
_ONEDRIVE_PROVEDORES = "Software\\SyncEngines\\Providers\\OneDrive"
_CACHE = {"db": None, "carimbo": None, "raizes": ()}


def _declaradas() -> list[str]:
    bruto = os.environ.get(VARIAVEL_RAIZES) or ""
    return [p.strip().strip('"').strip() for p in bruto.split(";") if p.strip().strip('"').strip()]


def _google_drive() -> tuple[str, ...]:
    base = os.environ.get("LOCALAPPDATA") or ""
    if not base:
        return ()
    db = os.path.join(base, *_DRIVE_DB)
    try:
        carimbo = os.path.getmtime(db)
    except OSError:
        return ()
    if _CACHE["db"] == db and _CACHE["carimbo"] == carimbo:
        return _CACHE["raizes"]
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
        try:
            raizes = tuple(str(p) for (p,) in con.execute(
                "select last_seen_absolute_path from roots") if p)
        finally:
            con.close()
    except sqlite3.Error:
        return ()
    _CACHE.update(db=db, carimbo=carimbo, raizes=raizes)
    return raizes


def _varint(dados: bytes, i: int) -> tuple[int, int]:
    valor, desloca = 0, 0
    while True:
        if i >= len(dados) or desloca > 63:
            raise ValueError("varint")
        b = dados[i]
        i += 1
        valor |= (b & 0x7F) << desloca
        if not b & 0x80:
            return valor, i
        desloca += 7


def _caminhos_protobuf(dados: bytes, profundidade: int = 0) -> list[str]:
    """Os textos com cara de caminho de uma mensagem protobuf, em ordem.

    `SyncTargets` é uma mensagem protobuf sem esquema publicado. Campo de
    tamanho variável que decodifica como caminho é caminho; os outros são
    tentados como mensagem aninhada. Estrutura inválida levanta ValueError.
    """
    achados, i = [], 0
    while i < len(dados):
        chave, i = _varint(dados, i)
        tipo = chave & 7
        if tipo == 0:
            _, i = _varint(dados, i)
        elif tipo in (1, 5):
            i += 8 if tipo == 1 else 4
        elif tipo == 2:
            n, i = _varint(dados, i)
            bloco = dados[i:i + n]
            if len(bloco) != n:
                raise ValueError("tamanho")
            i += n
            try:
                texto = bloco.decode("utf-8")
            except UnicodeError:
                texto = None
            if texto is not None and _PARECE_CAMINHO.match(texto):
                achados.append(texto)
            elif profundidade < 6:
                try:
                    achados.extend(_caminhos_protobuf(bloco, profundidade + 1))
                except ValueError:
                    pass
        else:
            raise ValueError("tipo")
        if i > len(dados):
            raise ValueError("fim")
    return achados


def _ler_sync_targets() -> bytes | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _DRIVE_REGISTRO) as k:
            valor, tipo = winreg.QueryValueEx(k, "SyncTargets")
    except OSError:
        return None
    return valor if tipo == winreg.REG_BINARY and isinstance(valor, bytes) else None


def _google_drive_registro() -> list[str]:
    dados = _ler_sync_targets()
    if not dados:
        return []
    try:
        return _caminhos_protobuf(dados)
    except ValueError:
        return []


def _onedrive_ambiente() -> list[str]:
    return [os.environ[v] for v in _ONEDRIVE_VARIAVEIS if os.environ.get(v)]


def _valores(chave_mae: str, valor: str) -> list[str]:
    import winreg
    achados = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, chave_mae) as mae:
            i = 0
            while True:
                try:
                    filha = winreg.EnumKey(mae, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(mae, filha) as k:
                        dado = winreg.QueryValueEx(k, valor)[0]
                except OSError:
                    continue
                if isinstance(dado, str) and dado.strip():
                    achados.append(dado.strip())
    except OSError:
        pass
    return achados


def _onedrive_registro() -> list[str]:
    return _valores(_ONEDRIVE_CONTAS, "UserFolder") + _valores(_ONEDRIVE_PROVEDORES, "MountPoint")


def raizes_sincronizadas() -> tuple[str, ...]:
    return tuple(_declaradas()) + _google_drive() + tuple(_google_drive_registro()) \
        + tuple(_onedrive_ambiente()) + tuple(_onedrive_registro())


def _normal(caminho: str) -> str:
    # "G:" sozinho é a pasta corrente da unidade G, e não a raiz dela.
    if re.fullmatch(r"[A-Za-z]:", caminho):
        caminho += "\\"
    try:
        pleno = os.path.realpath(caminho)
    except (OSError, ValueError):
        pleno = os.path.abspath(caminho)
    return pleno.replace("/", "\\").rstrip("\\").casefold()


def em_pasta_sincronizada(caminho: Path) -> bool:
    alvo = _normal(str(caminho))
    for raiz in raizes_sincronizadas():
        r = _normal(raiz)
        # O separador no fim impede que a raiz AI_IMPORT case AI_IMPORTANTE.
        if r and (alvo == r or alvo.startswith(r + "\\")):
            return True
    return False
