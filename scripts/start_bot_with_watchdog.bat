@echo off
REM E2: Start trading bot with watchdog supervisor
REM The watchdog monitors heartbeat.json and restarts the bot on failure.
REM
REM Usage:
REM   scripts\start_bot_with_watchdog.bat
REM
REM Optional env vars:
REM   set TRADING_PROFILE=prop_funded
REM   set MAX_RESTARTS_PER_HOUR=3

echo [WATCHDOG] Starting process supervisor...
echo [WATCHDOG] Working directory: %CD%
echo [WATCHDOG] Press Ctrl+C to stop

python -m core.watchdog
