from pathlib import Path

import checar_licencas as cl


def _pacote(pasta: Path, nome: str, **campos: str) -> None:
    info = pasta / f"{nome.replace('-', '_')}-1.0.dist-info"
    info.mkdir()
    linhas = ["Metadata-Version: 2.4", f"Name: {nome}", "Version: 1.0"]
    for chave, valor in campos.items():
        linhas.append(f"{chave.replace('_', '-')}: {valor}")
    (info / "METADATA").write_text("\n".join(linhas) + "\n", encoding="utf-8")


def test_gpl_plantado_reprova(tmp_path):
    _pacote(tmp_path, "falso-gpl", License_Expression="GPL-3.0-only")
    assert cl.verificar([str(tmp_path)]) == ["falso-gpl 1.0: GPL-3.0-only"]


def test_mit_e_lgpl_passam(tmp_path):
    _pacote(tmp_path, "falso-mit", License_Expression="MIT")
    _pacote(tmp_path, "falso-lgpl", Classifier=(
        "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)"))
    assert cl.verificar([str(tmp_path)]) == []


def test_licenca_desconhecida_reprova(tmp_path):
    _pacote(tmp_path, "falso-mudo")
    assert cl.verificar([str(tmp_path)]) == ["falso-mudo 1.0: licenca desconhecida"]


def test_basta_uma_alternativa_permitida(tmp_path):
    _pacote(tmp_path, "falso-duplo", License_Expression="GPL-2.0-only OR MIT")
    assert cl.verificar([str(tmp_path)]) == []


def test_texto_livre_de_licenca_e_reconhecido(tmp_path):
    _pacote(tmp_path, "falso-texto", License=(
        "Permission is hereby granted, free of charge, to any person"))
    assert cl.verificar([str(tmp_path)]) == []


def test_runtime_do_gcc_com_excecao_passa(tmp_path):
    _pacote(tmp_path, "falso-fortran", License_Expression=(
        "BSD-3-Clause AND GPL-3.0-or-later WITH GCC-exception-3.1"))
    assert cl.verificar([str(tmp_path)]) == []


def test_expressao_spdx_no_campo_license(tmp_path):
    _pacote(tmp_path, "falso-dupla", License="MPL-2.0 AND MIT")
    assert cl.verificar([str(tmp_path)]) == []


def test_classificador_vence_texto_composto(tmp_path):
    _pacote(tmp_path, "falso-composto",
            License="Inclui o texto da GNU Affero General Public License",
            Classifier="License :: OSI Approved :: BSD License")
    assert cl.verificar([str(tmp_path)]) == []


def test_ambiente_real_esta_limpo():
    assert cl.verificar() == []
