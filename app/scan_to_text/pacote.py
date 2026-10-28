"""Pacote de regras de um idioma.

Todo YAML de `app/packs/<idioma>/` vira uma seção, com o nome do arquivo sem
extensão. O motor lê daqui rótulos, meses, títulos e padrões; o código dele
não tem texto de idioma. Lido como `utf-8-sig`, porque o Bloco de Notas do
LTSB grava BOM.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

RAIZ_PACKS = Path(__file__).resolve().parents[1] / "packs"


class PacoteAusente(LookupError):
    pass


@dataclass(frozen=True)
class Pacote:
    idioma: str
    secoes: dict[str, Any]

    def __getitem__(self, secao: str) -> Any:
        return self.secoes[secao]


def carregar(idioma: str, raiz: Path = RAIZ_PACKS) -> Pacote:
    pasta = raiz / idioma
    if not pasta.is_dir():
        raise PacoteAusente(idioma)
    secoes = {arquivo.stem: yaml.safe_load(arquivo.read_text(encoding="utf-8-sig")) or {}
              for arquivo in sorted(pasta.glob("*.yaml"))}
    return Pacote(idioma, secoes)
