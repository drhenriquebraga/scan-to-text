@echo off
REM ============================================================
REM  TESTAR.cmd - kit de diagnostico do Scan-to-Text
REM
REM  Dois cliques aqui. Nao precisa de administrador e nao instala
REM  nada: apagar a pasta do kit desfaz tudo.
REM
REM  Roda o preflight e o autoteste com o Python do kit e grava o
REM  resultado em resultados\<MAQUINA>.txt, ao lado deste arquivo.
REM  O resultado nao leva dado de paciente: so versoes, tempos e
REM  memoria. Esse arquivo e tambem o log do kit.
REM
REM  Regra de ambiente: ASCII puro, CRLF e sem troca de pagina de
REM  codigo. O cmd.exe le o lote por offset de byte.
REM ============================================================
setlocal
set "KIT=%~dp0"
set "PY=%KIT%Scan-to-Text\python\python.exe"

REM  Dois cliques no .cmd de dentro do .zip rodam de uma pasta
REM  temporaria, sem o resto do kit. A marca e o python.exe ao lado,
REM  e nao o nome da pasta temporaria, que muda entre Windows.
if exist "%PY%" goto :kit_ok

echo.
echo   ============================================================
echo    O ARQUIVO .ZIP AINDA NAO FOI EXTRAIDO
echo   ============================================================
echo.
echo    Voce abriu este arquivo de dentro do .zip. Assim o Windows
echo    traz so este arquivo, sem o resto do kit.
echo.
echo    O QUE FAZER:
echo      1. Feche esta janela.
echo      2. Clique com o botao direito no arquivo .zip.
echo      3. Escolha "Extrair tudo..." e confirme.
echo      4. Abra a pasta extraida e de dois cliques em TESTAR.cmd.
echo   ============================================================
echo.
pause
exit /b 1

:kit_ok
set "RES=%KIT%resultados"
if not exist "%RES%" mkdir "%RES%"
set "SAIDA=%RES%\%COMPUTERNAME%.txt"
set "HF_HUB_OFFLINE=1"

echo.
echo   Scan-to-Text - kit de diagnostico
echo   Resultado em: "%SAIDA%"
echo   O autoteste pode levar alguns minutos. Nao feche esta janela.
echo.

> "%SAIDA%" echo ==== kit de diagnostico do Scan-to-Text
>> "%SAIDA%" echo inicio: %DATE% %TIME%
>> "%SAIDA%" echo pasta do kit: "%KIT%"

REM  O WebView2 Fixed nao abre no Windows 10 sem leitura e execucao
REM  para os SIDs de pacote de aplicativo. Quem extraiu o kit e dono
REM  da pasta, entao o icacls roda sem administrador.
REM  O redirecionamento vem antes do echo: um algarismo colado no
REM  sinal de redirecionar vira o numero do fluxo e some da linha.
>> "%SAIDA%" echo ==== icacls do webview2
icacls "%KIT%Scan-to-Text\webview2" /grant *S-1-15-2-1:(OI)(CI)(RX) >> "%SAIDA%" 2>&1
icacls "%KIT%Scan-to-Text\webview2" /grant *S-1-15-2-2:(OI)(CI)(RX) >> "%SAIDA%" 2>&1

echo   1 de 2: preflight
>> "%SAIDA%" echo ==== preflight
"%PY%" -m scan_to_text preflight >> "%SAIDA%" 2>&1
>> "%SAIDA%" echo codigo do preflight: %ERRORLEVEL%

echo   2 de 2: autoteste
>> "%SAIDA%" echo ==== autoteste
"%PY%" -m scan_to_text autoteste >> "%SAIDA%" 2>&1
set "RC=%ERRORLEVEL%"
>> "%SAIDA%" echo codigo do autoteste: %RC%
>> "%SAIDA%" echo fim: %DATE% %TIME%

echo.
if not "%RC%"=="0" echo   O autoteste terminou com o codigo %RC%. O arquivo explica o motivo.
echo   ============================================================
echo    PRONTO. Leve de volta a pasta "resultados" do kit.
echo    Arquivo: "%SAIDA%"
echo   ============================================================
echo.
pause
exit /b 0
