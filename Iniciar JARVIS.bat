@echo off
rem Abre JARVIS con doble clic, sin dejar una consola abierta.
cd /d "%~dp0"
start "" pythonw jarvis.py
