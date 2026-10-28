"""Monta o kit de diagnóstico: o motor completo e o TESTAR.cmd.

O kit leva a mesma pasta `Scan-to-Text` do pacote (Python embutido, rodas,
modelos, WebView2 e runtime Visual C++ quando houver) e, na raiz do
zip, só o `TESTAR.cmd`, que roda o preflight e o autoteste e grava
`resultados\\<MAQUINA>.txt` na pasta do kit. Nada fica instalado: apagar a
pasta desfaz tudo.

O kit não vai a público e roda antes de existir a cópia pública; por isso a
fonte padrão é a oficina. Não leva o instalador nem o LEIA-ME dele.

Uso: python tools/montar_kit.py   ->   _montagem/kit/SCAN_TO_TEXT_KIT_v<versão>.zip
"""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "packager"))

import baixar  # noqa: E402
import montar_pacote as mp  # noqa: E402

SAIDA = mp.MONTAGEM / "kit"


def montar_kit(fonte: Path = RAIZ, saida: Path = SAIDA, *, catalogo: dict | None = None,
               terceiros: Path = baixar.TERCEIROS, modelos: Path = mp.MODELOS, vs_raizes=None,
               extrair_cab=mp.extrair_cab, dizer=print) -> Path:
    versao = mp.versao_do_projeto(fonte)
    pasta_app = saida / "montagem" / mp.NOME
    avisos = mp.montar_arvore(fonte, pasta_app, catalogo or baixar.ler_catalogo(), terceiros,
                              modelos, vs_raizes=vs_raizes, extrair_cab=extrair_cab,
                              leia_me=False, dizer=dizer)
    kit = mp.gerar_zip(pasta_app, fonte / "diagnostico" / "TESTAR.cmd",
                       saida / f"{mp.PROJETO}_KIT_v{versao}.zip")
    for aviso in avisos:
        dizer(f"  aviso: {aviso}")
    return kit


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        kit = montar_kit()
    except mp.MontagemRecusada as erro:
        print(f"PAROU: {erro}")
        return 1
    print(f"kit: {kit} ({kit.stat().st_size / 2**30:.2f} GiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
