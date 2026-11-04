@echo off
python "%~dp0install.py" --remove || py "%~dp0install.py" --remove
pause
