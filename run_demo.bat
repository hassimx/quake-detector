@echo off
chcp 65001 >nul
rem one-click demo: environment, records, server, browser, replay
rem the file must be in the project root. Lines end with CRLF (cmd needs that)
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title Quake Detector - demo

echo ==================================================
echo   Quake Detector: демо одним щелчком
echo ==================================================
echo.

rem 1. virtual environment
if exist ".venv\Scripts\python.exe" goto have_venv
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto no_python
echo [1/6] Создаю виртуальное окружение .venv ...
%PY% -m venv .venv
if errorlevel 1 goto venv_failed
:have_venv

rem 2. libraries (only the first time, then the .demo_ready mark)
if exist ".venv\.demo_ready" goto have_packages
echo [2/6] Устанавливаю библиотеки, это только в первый раз, нужен интернет ...
".venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-server.txt
if errorlevel 1 goto pip_failed
echo ok> ".venv\.demo_ready"
:have_packages

rem 3. station records
echo [3/6] Проверяю записи станций в папке data ...
".venv\Scripts\python.exe" -m server.prepare_demo
if errorlevel 1 goto prepare_failed

rem 4. clean start
echo [4/6] Чистый старт: удаляю старую базу ...
if exist "server\quake.db" del /q "server\quake.db"
".venv\Scripts\python.exe" -m server.wait_server --free
if errorlevel 1 goto port_busy

rem 5. server in a separate window, then the browser
echo [5/6] Запускаю сервер в отдельном окне и жду, пока он оживёт ...
start "Quake Detector SERVER" cmd /k .venv\Scripts\python.exe -m uvicorn server.app:create_app --factory --port 8000
".venv\Scripts\python.exe" -m server.wait_server 90
if errorlevel 1 goto server_failed
echo Открываю страницу в браузере: http://127.0.0.1:8000
start "" "http://127.0.0.1:8000"

rem 6. replay of the records
echo [6/6] Воспроизвожу записи землетрясения 2021-10-07 в 60 раз быстрее ...
echo      Смотрите страницу: события появятся сами.
echo.
".venv\Scripts\python.exe" -m server.replay quake:2021-10-07 --speed 60
if errorlevel 1 goto replay_failed

echo.
echo ==================================================
echo   Готово. Что делать дальше:
echo   1. Посмотрите страницу в браузере: http://127.0.0.1:8000
echo      Кликните по событию в списке справа, чтобы увидеть сигналы станций.
echo   2. Когда закончите, закройте окно сервера "Quake Detector SERVER".
echo   3. Чтобы пустить демо заново, снова дважды щёлкните run_demo.bat.
echo ==================================================
pause
exit /b 0

:no_python
echo.
echo Не найден Python 3. Установите его с https://www.python.org/downloads/
echo и при установке отметьте галочку "Add python.exe to PATH". Потом запустите демо снова.
pause
exit /b 1

:venv_failed
echo.
echo Не удалось создать виртуальное окружение. Проверьте, что установлен настоящий
echo Python 3.10 или новее с python.org, и запустите демо снова.
pause
exit /b 1

:pip_failed
echo.
echo Не удалось установить библиотеки. Проверьте интернет и запустите демо снова.
pause
exit /b 1

:prepare_failed
echo.
echo Подготовка записей не удалась, см. сообщение выше. Демо не запущено.
pause
exit /b 1

:port_busy
echo.
pause
exit /b 1

:server_failed
echo.
echo Сервер не запустился. Посмотрите окно "Quake Detector SERVER": там причина.
pause
exit /b 1

:replay_failed
echo.
echo Воспроизведение записей остановилось с ошибкой, см. сообщение выше.
pause
exit /b 1
