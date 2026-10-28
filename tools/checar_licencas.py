"""Portão de licenças: reprova pacote instalado com licença fora da lista.

Permitidas: MIT e variantes, BSD, Apache-2.0, ISC, PSF, MPL-2.0, LGPL,
CDLA-Permissive (dados de modelo) e HPND. Proibidas: GPL, AGPL e licença
desconhecida. Pacote cujo metadado não diz a licença, conferido à mão no
repositório do projeto, entra em CONHECIDOS com a licença verificada.
"""
import importlib.metadata as md
import re
import sys

PERMITIDA = re.compile(
    r"(MIT(-CMU|-0)?|0?BSD(-[23]-Clause)?|Apache-2\.0|ISC|PSF-2\.0|Python-2\.0"
    r"|CNRI-Python|MPL-2\.0|LGPL-(2\.1|3\.0)(-only|-or-later)?"
    r"|CDLA-Permissive-[12]\.0|HPND|Zlib|BSL-1\.0|CC0-1\.0|GCC-exception-3\.1)$")
# GPL com a GCC Runtime Library Exception é o runtime do compilador embutido em
# rodas compiladas; a exceção permite distribuí-lo junto de código não-GPL.
GPL_COM_RUNTIME_GCC = re.compile(
    r"GPL-3\.0(-only|-or-later)?\s+WITH\s+GCC-exception-3\.1")
EXPRESSAO_SPDX = re.compile(r"[\w.+-]+(\s+(AND|OR|WITH)\s+[\w.+-]+)*")

CONHECIDOS = {
    # LICENSE dentro do pacote: MIT. O metadado não declara a licença.
    "clr-loader": "MIT",
    # O PDFium embutido traz dependências de licença permissiva. O texto da GPL
    # no icu.txt é do aclocal.m4, macro de build do ICU4C que não vai no binário.
    "pypdfium2": "Apache-2.0 OR BSD-3-Clause",
    # A roda do Windows leva o runtime do gfortran sob a GCC Runtime Library
    # Exception, além de LAPACK e OpenBLAS (BSD).
    "scipy": "BSD-3-Clause AND GPL-3.0-or-later WITH GCC-exception-3.1",
}

CURTOS = {
    "MIT": "MIT", "MIT LICENSE": "MIT",
    "BSD": "BSD", "BSD LICENSE": "BSD", "BSD-3-CLAUSE": "BSD-3-Clause",
    "3-CLAUSE BSD LICENSE": "BSD-3-Clause",
    "APACHE 2.0": "Apache-2.0", "APACHE-2.0": "Apache-2.0",
    "APACHE 2.0 LICENSE": "Apache-2.0", "APACHE LICENSE 2.0": "Apache-2.0",
    "APACHE SOFTWARE LICENSE": "Apache-2.0",
    "PSF": "PSF-2.0", "ISC": "ISC", "MPL-2.0": "MPL-2.0", "HPND": "HPND",
}
# Campo License com o texto inteiro da licença: a frase característica decide.
# A ordem importa: LESSER antes de GENERAL, porque o texto da LGPL contém os dois.
TEXTO = (
    ("GNU AFFERO", "AGPL"),
    ("GNU LESSER", "LGPL-3.0"),
    ("GNU GENERAL PUBLIC", "GPL"),
    ("PERMISSION IS HEREBY GRANTED, FREE OF CHARGE", "MIT"),
    ("REDISTRIBUTION AND USE IN SOURCE AND BINARY FORMS", "BSD"),
    ("APACHE LICENSE", "Apache-2.0"),
)
CLASSIFICADORES = {
    "MIT License": "MIT",
    "BSD License": "BSD",
    "Apache Software License": "Apache-2.0",
    "ISC License (ISCL)": "ISC",
    "Python Software Foundation License": "PSF-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "Historical Permission Notice and Disclaimer (HPND)": "HPND",
    "GNU Lesser General Public License v2 (LGPLv2)": "LGPL-2.1",
    "GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.1-or-later",
    "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0",
    "GNU Lesser General Public License v3 or later (LGPLv3+)": "LGPL-3.0-or-later",
}


def _nome(dist: md.Distribution) -> str:
    return re.sub(r"[-_.]+", "-", dist.name).lower()


def _permitida(expressao: str) -> bool:
    expressao = GPL_COM_RUNTIME_GCC.sub("GCC-exception-3.1", expressao)
    expressao = re.sub(r"\s+WITH\s+[\w.+-]+", "", expressao)
    expressao = expressao.replace("(", " ").replace(")", " ")
    alternativas = re.split(r"\s+OR\s+", expressao.strip())
    return any(
        all(PERMITIDA.match(t.strip()) for t in re.split(r"\s+AND\s+", a.strip()))
        for a in alternativas)


def licenca(dist: md.Distribution) -> str | None:
    nome = _nome(dist)
    if nome in CONHECIDOS:
        return CONHECIDOS[nome]
    meta = dist.metadata
    if meta.get("License-Expression"):
        return meta["License-Expression"].strip()
    campo = (meta.get("License") or "").strip()
    if campo.upper() in CURTOS:
        return CURTOS[campo.upper()]
    for classificador in meta.get_all("Classifier") or []:
        if classificador.startswith("License :: "):
            ultimo = classificador.split(" :: ")[-1]
            if ultimo in CLASSIFICADORES:
                return CLASSIFICADORES[ultimo]
            if "General Public License" in ultimo and "Lesser" not in ultimo:
                return "GPL"
    # Campo License com expressão SPDX só depois do classificador: palavra solta
    # como "Apache" ou "PSFL" também tem a forma de expressão, mas não é SPDX.
    if EXPRESSAO_SPDX.fullmatch(campo):
        return campo
    # Texto inteiro da licença é o último recurso: licença composta cita outras
    # (o texto da GPL-3.0 menciona a Affero), então a frase só decide sozinha.
    for frase, spdx in TEXTO:
        if frase in campo.upper():
            return spdx
    return None


def verificar(caminhos: list[str] | None = None) -> list[str]:
    dists = md.distributions(path=caminhos) if caminhos is not None else md.distributions()
    problemas, vistos = [], set()
    for dist in dists:
        nome = _nome(dist)
        if nome in vistos:
            continue
        vistos.add(nome)
        lic = licenca(dist)
        if lic is None:
            problemas.append(f"{nome} {dist.version}: licenca desconhecida")
        elif not _permitida(lic):
            problemas.append(f"{nome} {dist.version}: {lic}")
    return sorted(problemas)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    problemas = verificar()
    for p in problemas:
        print(p)
    print("licencas: ok" if not problemas else f"licencas: {len(problemas)} fora da lista")
    return 1 if problemas else 0


if __name__ == "__main__":
    raise SystemExit(main())
