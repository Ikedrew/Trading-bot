@echo off
title Research Lab server
cd /d "%~dp0"
python -m research_engine.v10.lab.server --open
if errorlevel 1 pause
