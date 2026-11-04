@echo off
python "%~dp0install.py" %* || py "%~dp0install.py" %*
pause
