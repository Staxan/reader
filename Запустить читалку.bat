@echo off
rem Читалка книги. Путь к базе берётся из config.json в корне Orchestra.
rem Открыть: http://localhost:8765
cd /d "%~dp0"
title Orchestra - Читалка книги
python reader\book_reader.py %*
if errorlevel 1 pause
