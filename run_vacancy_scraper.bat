@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Creating project Python environment...
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv "%~dp0.venv"
    ) else (
        python -m venv "%~dp0.venv"
    )
    if errorlevel 1 (
        echo.
        echo Could not create a virtual environment. Install Python 3 and try again.
        pause
        exit /b 1
    )
)

if not exist "%PYTHON%" (
    echo.
    echo Virtual environment was not created successfully.
    pause
    exit /b 1
)

"%PYTHON%" -c "import requests, bs4, nepali_datetime, pypdf" >nul 2>nul
if errorlevel 1 (
    echo Installing scraper dependencies. This requires an internet connection...
    "%PYTHON%" -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo.
        echo Dependency installation failed. Check your internet connection and try again.
        pause
        exit /b 1
    )
)

"%PYTHON%" "%~dp0it_vacancy_scraper_v2.py"
if errorlevel 1 (
    echo.
    echo Scraper failed. Review the error above.
    pause
    exit /b 1
)

if not exist "%~dp0vacancy_notices.html" (
    echo.
    echo Scraper finished but the HTML report was not created.
    pause
    exit /b 1
)

start "" "%~dp0vacancy_notices.html"
endlocal
