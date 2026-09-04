@echo off
rem Проверка связи читалки с Никой в Hermes.
cd /d "%~dp0"
title Orchestra - Проверка связи с Никой
python reader\hermes_link.py --test
echo.
echo Если Hermes недоступен, запустите шлюз профиля nika-redaktor:
echo   hermes gateway run --profile nika-redaktor
echo.
pause
