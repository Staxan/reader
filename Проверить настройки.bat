@echo off
rem Проверка настроек: где лежит база, сколько в ней документов.
cd /d "%~dp0"
title Orchestra - Проверка настроек
python reader\settings.py
echo.
pause
