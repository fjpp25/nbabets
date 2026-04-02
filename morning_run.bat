@echo off
:: ============================================================
::  NBA Betting Dry Run — Morning Script
::  Run this every morning. It will:
::    1. Settle yesterday's results
::    2. Show the cumulative report
::    3. Regenerate the HTML dashboard
::    4. Run today's dry run (picks for tonight's games)
:: ============================================================

cd /d "%~dp0"
set PYTHON=.venv\Scripts\python.exe

echo.
echo ============================================================
echo   NBA BETTING -- MORNING RUN
echo   %date% %time%
echo ============================================================
echo.

:: Step 1: Settle yesterday's results
echo [1/4] Settling yesterday's results...
echo ------------------------------------------------------------
%PYTHON% results_tracker.py
if errorlevel 1 (
    echo WARNING: results_tracker.py encountered an error.
    echo This is normal if yesterday's games are not finished yet.
    echo.
)

:: Step 2: Show cumulative report
echo [2/4] Generating cumulative report...
echo ------------------------------------------------------------
%PYTHON% report.py
if errorlevel 1 (
    echo WARNING: report.py encountered an error.
    echo.
)

:: Step 3: Regenerate dashboard
echo [3/4] Updating dashboard...
echo ------------------------------------------------------------
%PYTHON% dashboard.py
if errorlevel 1 (
    echo WARNING: dashboard.py encountered an error.
    echo.
)

:: Step 4: Today's picks
echo [4/4] Fetching today's picks...
echo ------------------------------------------------------------
%PYTHON% dry_run.py
if errorlevel 1 (
    echo ERROR: dry_run.py failed. Check your API keys and internet connection.
    echo.
)

echo.
echo ============================================================
echo   Done! Open dashboard.html to view your stats.
echo ============================================================
echo.
pause
