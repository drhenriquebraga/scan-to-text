"""Recipientes: ZIP (até 2 níveis), HEIC e arquivos simples.

Tudo é extraído numa pasta de trabalho local, com nome neutro e numerado: o
nome que veio no ZIP não é usado como caminho (evita escapar da pasta) nem vai
para o log. O caminho dentro do ZIP fica em `Item.dentro`, para o manifesto.
Formato fora da v1 é ignorado e listado.
"""
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from PIL import Image

from scan_to_text.ler.pdf import Ilegivel

NIVEL_MAXIMO = 2
LIMITE_BYTES = 1_000_000_000
LIMITE_ARQUIVOS = 1000
TIPOS = {".pdf": "pdf", ".docx": "docx", ".tif": "tiff", ".tiff": "tiff",
         ".png": "imagem", ".jpg": "imagem", ".jpeg": "imagem", ".bmp": "imagem",
         ".gif": "imagem", ".webp": "imagem"}
HEIF = {".heic", ".heif"}


@dataclass(frozen=True)
class Item:
    caminho: Path
    tipo: str
    dentro: str


def _heic_para_png(arquivo: Path, pasta: Path, numero: int) -> Path:
    import pi_heif

    pi_heif.register_heif_opener()
    destino = pasta / f"{numero:04d}.png"
    with Image.open(arquivo) as imagem:
        imagem.convert("RGB").save(destino, format="PNG")
    return destino


class _Expansor:
    def __init__(self, pasta: Path):
        self.pasta = pasta
        self.numero = 0
        self.itens: list[Item] = []
        self.ignorados: list[str] = []

    def proximo(self, sufixo: str) -> Path:
        self.numero += 1
        self.pasta.mkdir(parents=True, exist_ok=True)
        return self.pasta / f"{self.numero:04d}{sufixo}"

    def arquivo(self, caminho: Path, dentro: str, nivel: int) -> None:
        sufixo = caminho.suffix.lower()
        if sufixo == ".zip":
            if nivel >= NIVEL_MAXIMO:
                self.ignorados.append(dentro)
                return
            self.zip(caminho, dentro, nivel + 1)
        elif sufixo in HEIF:
            self.numero += 1
            self.pasta.mkdir(parents=True, exist_ok=True)
            self.itens.append(Item(_heic_para_png(caminho, self.pasta, self.numero), "imagem", dentro))
        elif sufixo in TIPOS:
            self.itens.append(Item(caminho, TIPOS[sufixo], dentro))
        else:
            self.ignorados.append(dentro)

    def zip(self, caminho: Path, dentro: str, nivel: int) -> None:
        try:
            arquivo_zip = zipfile.ZipFile(caminho)
        except zipfile.BadZipFile as erro:
            raise Ilegivel("corrompido") from erro
        with arquivo_zip:
            entradas = [e for e in arquivo_zip.infolist() if not e.is_dir()]
            if any(e.flag_bits & 0x1 for e in entradas):
                raise Ilegivel("senha")
            if (len(entradas) > LIMITE_ARQUIVOS
                    or sum(e.file_size for e in entradas) > LIMITE_BYTES):
                raise Ilegivel("zip_grande")
            for entrada in entradas:
                nome = PurePosixPath(entrada.filename.replace("\\", "/"))
                interno = f"{dentro}/{nome}" if dentro else str(nome)
                extraido = self.proximo(nome.suffix.lower())
                with arquivo_zip.open(entrada) as leitura, open(extraido, "wb") as escrita:
                    while bloco := leitura.read(1 << 20):
                        escrita.write(bloco)
                self.arquivo(extraido, interno, nivel)


def expandir(arquivo: Path, pasta: Path) -> tuple[list[Item], list[str]]:
    """Devolve (itens para ler, caminhos ignorados dentro do recipiente)."""
    expansor = _Expansor(pasta)
    if arquivo.suffix.lower() == ".zip":
        expansor.zip(arquivo, "", 1)
    elif arquivo.suffix.lower() in HEIF:
        expansor.arquivo(arquivo, "", 0)
    elif arquivo.suffix.lower() in TIPOS:
        expansor.itens.append(Item(arquivo, TIPOS[arquivo.suffix.lower()], ""))
    else:
        expansor.ignorados.append(arquivo.suffix.lower())
    return expansor.itens, expansor.ignorados
