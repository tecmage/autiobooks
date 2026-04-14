@echo off
setlocal enabledelayedexpansion

REM Resolve directories — script lives in windows/, project root is parent
set SCRIPT_DIR=%~dp0
set PROJECT_ROOT=%SCRIPT_DIR%..
cd /d "%SCRIPT_DIR%"

echo ========================================
echo Autiobooks Windows Build Script
echo ========================================
echo.

REM Check Python 3.12
py -3.12 --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3.12 not found. Please install Python 3.12.
    exit /b 1
)

echo Detected Python 3.12 with CUDA support

REM Create virtual environment if it doesn't exist
if not exist "venv312" (
    echo.
    echo Creating virtual environment with Python 3.12...
    py -3.12 -m venv venv312
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        exit /b 1
    )
)

REM Set explicit paths to venv Python and pip
set VENV_PYTHON=%CD%\venv312\Scripts\python.exe
set VENV_PIP=%CD%\venv312\Scripts\pip.exe
set VENV_PYINSTALLER=%CD%\venv312\Scripts\pyinstaller.exe

REM Verify venv Python
"%VENV_PYTHON%" --version

echo.
echo ========================================
echo Installing build dependencies
echo ========================================

REM Upgrade pip
"%VENV_PIP%" install --upgrade pip

REM Install PyInstaller
"%VENV_PIP%" install pyinstaller

REM Install CPU-only torch (CUDA will be downloaded on demand for NVIDIA GPUs)
echo.
echo Installing torch (CPU-only - CUDA downloaded on demand)...
"%VENV_PIP%" install torch --index-url https://download.pytorch.org/whl/cpu

echo.
echo ========================================
echo Installing runtime dependencies
echo ========================================

REM Kokoro has complex dependencies with version conflicts
REM Install kokoro 0.9.4 with --no-deps
"%VENV_PIP%" install pillow numpy
"%VENV_PIP%" install "kokoro==0.9.4" --no-deps
"%VENV_PIP%" install loguru huggingface-hub "misaki>=0.9.4,<0.10.0" transformers num2words phonemizer-fork espeakng-loader addict
"%VENV_PIP%" install ebooklib soundfile pygame bs4 lxml tkinterdnd2

echo.
echo ========================================
echo Installing spacy for NLP processing
echo ========================================
"%VENV_PIP%" install spacy
"%VENV_PYTHON%" -m spacy download en_core_web_sm
if errorlevel 1 (
    echo WARNING: spacy installation failed. Speech quality may be reduced.
) else (
    echo spacy installed successfully
)

REM misaki is bundled in autiobooks/misaki/ with en.py pre-patched to make spacy optional
echo Using bundled misaki with spacy support

echo.
echo ========================================
echo Downloading ffmpeg
echo ========================================

REM Download latest ffmpeg
if not exist "ffmpeg-master-latest-win64-gpl" (
    echo Downloading ffmpeg [~150MB]...
    curl -L -o ffmpeg.zip "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
    echo Extracting ffmpeg...
    tar -xf ffmpeg.zip
    del ffmpeg.zip
) else (
    echo ffmpeg already downloaded
)

echo.
echo ========================================
echo Downloading espeak-ng
echo ========================================

REM espeak-ng Windows binaries no longer hosted on GitHub releases
REM Use scoop to install espeak-ng which provides the exe
where scoop >nul 2>&1
if %errorlevel% equ 0 (
    if not exist "espeak-ng" (
        echo Installing espeak-ng via scoop...
        call scoop install espeak-ng
    ) else (
        echo espeak-ng already downloaded
    )
) else (
    REM Fallback: try chocolatey
    where choco >nul 2>&1
    if %errorlevel% equ 0 (
        if not exist "espeak-ng" (
            echo Installing espeak-ng via chocolatey...
            choco install espeak-ng -y
        ) else (
            echo espeak-ng already downloaded
        )
    ) else (
        echo WARNING: Neither scoop nor chocolatey found. Please install espeak-ng manually.
    )
)

REM Copy espeak-ng to dist folder after installation
if exist "%USERPROFILE%\scoop\shims\espeak-ng.exe" (
    if not exist "espeak-ng" mkdir espeak-ng
    copy "%USERPROFILE%\scoop\shims\espeak-ng.exe" espeak-ng\espeak-ng.exe
)

echo.
echo ========================================
echo Verifying dependencies
echo ========================================
"%VENV_PYTHON%" -c "import torch; print(f'torch: {torch.__version__}'); print(f'CUDA: {torch.cuda.is_available()}')"
"%VENV_PYTHON%" -c "import kokoro; print('kokoro: OK')"
"%VENV_PYTHON%" -c "import PIL; print('PIL: OK')"
"%VENV_PYTHON%" -c "import soundfile; print('soundfile: OK')"
"%VENV_PYTHON%" -c "import pygame; print('pygame: OK')"
"%VENV_PYTHON%" -c "import lxml; print('lxml: OK')"
"%VENV_PYTHON%" -c "import ebooklib; print('ebooklib: OK')"
"%VENV_PYTHON%" -c "import bs4; print('bs4: OK')"

if exist "ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe" (
    echo ffmpeg: OK
) else (
    echo WARNING: ffmpeg not found in ffmpeg-master-latest-win64-gpl\bin
)

echo.
echo ========================================
echo Installing autiobooks package
echo ========================================

REM Install autiobooks itself so all transitive deps are resolved
"%VENV_PIP%" install "%PROJECT_ROOT%" 
if errorlevel 1 (
    echo.
    echo ERROR: Package install failed.
    exit /b 1
)

echo.
echo ========================================
echo Running PyInstaller
echo ========================================

REM Clean previous build
if exist "dist" (
    echo Cleaning previous build...
    rmdir /s /q dist
)
if exist "build" (
    rmdir /s /q build
)

REM Run PyInstaller
"%VENV_PYTHON%" -m PyInstaller autiobooks.spec --clean

if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller failed.
    exit /b 1
)

REM Replace bundled misaki/en.py with our patched version (makes spacy optional)
if exist "%PROJECT_ROOT%\autiobooks\misaki\en.py" (
    echo Replacing bundled misaki with patched version...
    copy /Y "%PROJECT_ROOT%\autiobooks\misaki\en.py" "dist\autiobooks\_internal\misaki\en.py"
)

REM Copy ffmpeg into the dist folder
if exist "ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe" (
    echo.
    echo ========================================
    echo Copying ffmpeg to distribution
    echo ========================================
    copy ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe dist\autiobooks\ffmpeg.exe
    copy ffmpeg-master-latest-win64-gpl\bin\ffplay.exe dist\autiobooks\ffplay.exe
    copy ffmpeg-master-latest-win64-gpl\bin\ffprobe.exe dist\autiobooks\ffprobe.exe
) else (
    echo WARNING: Could not copy ffmpeg - file not found
)

REM Copy espeak-ng into the dist folder
set ESPEAK_SRC=
if exist "espeak-ng\espeak-ng.exe" (
    set ESPEAK_SRC=espeak-ng\espeak-ng.exe
) else if exist "%USERPROFILE%\scoop\shims\espeak-ng.exe" (
    set ESPEAK_SRC=%USERPROFILE%\scoop\shims\espeak-ng.exe
)
if defined ESPEAK_SRC (
    copy %ESPEAK_SRC% dist\autiobooks\espeak-ng.exe
    echo espeak-ng copied
) else (
    echo WARNING: espeak-ng.exe not found
)

echo.
echo ========================================
echo Build Summary
echo ========================================
echo.
if exist "dist\autiobooks\autiobooks.exe" (
    echo Executable: dist\autiobooks\autiobooks.exe
    for %%A in ("dist\autiobooks\autiobooks.exe") do echo Size: %%~zA bytes
)
if exist "dist\autiobooks\ffmpeg.exe" (
    echo FFmpeg: dist\autiobooks\ffmpeg.exe
    for %%A in ("dist\autiobooks\ffmpeg.exe") do echo Size: %%~zA bytes
)
echo.
echo To run: dist\autiobooks\autiobooks.exe
echo.
