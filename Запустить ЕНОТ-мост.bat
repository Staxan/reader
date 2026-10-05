@echo off
rem ENOT-мост: локальный прокси к Yonote мимо VPN-туннеля.
rem Даёт агентам Hermes (Гера, Ника, Тим, Мира) доступ к ENOT из WSL.
rem Остановка: Ctrl+C в этом окне.
cd /d "%~dp0reader"
python enot_bridge.py
pause
