@echo off
REM ============================================================
REM  INSTALL.cmd - instalador do Scan-to-Text
REM
REM  Dois cliques aqui, depois de extrair o .zip. Nao precisa de
REM  administrador nem de internet. Copia o app para
REM  C:\RADIOTERAPIA_AI\SCAN_TO_TEXT, cria os atalhos e abre o app.
REM  Rodar de novo com o .zip de uma versao nova atualiza e mantem
REM  o config.json.
REM
REM  Desinstalar: INSTALL.cmd desinstalar
REM
REM  Este arquivo so acha o Python do pacote e chama o instalador,
REM  python -m scan_to_text.instalar, que faz todo o trabalho. O
REM  registro fica em INSTALL_LOG.txt, ao lado deste arquivo.
REM
REM  Regra de ambiente: ASCII puro, CRLF e sem troca de pagina de
REM  codigo. O cmd.exe le o lote por offset de byte.
REM ============================================================
setlocal
set "PY=%~dp0Scan-to-Text\python\python.exe"
set "LOG=%~dp0INSTALL_LOG.txt"

REM  Dois cliques no .cmd de dentro do .zip rodam de uma pasta
REM  temporaria, sem o resto do pacote. A marca e o python.exe ao
REM  lado, e nao o nome da pasta temporaria, que muda entre Windows.
if exist "%PY%" goto :pacote_ok

echo.
echo   ============================================================
echo    O ARQUIVO .ZIP AINDA NAO FOI EXTRAIDO
echo   ============================================================
echo.
echo    Voce abriu este arquivo de dentro do .zip. Assim o Windows
echo    traz so este arquivo, sem o resto do programa.
echo.
echo    O QUE FAZER:
echo      1. Feche esta janela.
echo      2. Clique com o botao direito no arquivo .zip.
echo      3. Escolha "Extrair tudo..." e confirme.
echo      4. Abra a pasta extraida e de dois cliques em INSTALL.cmd.
echo   ============================================================
echo.
pause
exit /b 1

:pacote_ok
> "%LOG%" echo ==== INSTALL.cmd do Scan-to-Text
>> "%LOG%" echo inicio: %DATE% %TIME%
>> "%LOG%" echo pasta: "%~dp0"
>> "%LOG%" echo argumentos: %*

echo.
echo   Scan-to-Text - instalador
echo   Nao feche esta janela ate aparecer PRONTO ou PAROU.
echo.
"%PY%" -m scan_to_text.instalar --log "%LOG%" %*
set "RC=%ERRORLEVEL%"
>> "%LOG%" echo codigo: %RC%
>> "%LOG%" echo fim: %DATE% %TIME%

echo.
if "%RC%"=="0" goto :fim
echo   ============================================================
echo    A INSTALACAO NAO TERMINOU, codigo %RC%.
echo    A mensagem acima diz o motivo. O registro fica em:
echo    "%LOG%"
echo   ============================================================
echo.
pause
exit /b %RC%

:fim
pause
exit /b 0
