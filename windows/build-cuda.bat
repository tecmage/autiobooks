@echo off
setlocal enabledelayedexpansion

REM Resolve directories — script lives in windows/, project root is parent
set SCRIPT_DIR=%~dp0
set PROJECT_ROOT=%SCRIPT_DIR%..
cd /d "%SCRIPT_DIR%"

echo ========================================
echo Autiobooks CUDA Build Script
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
if not exist "venv312-cuda" (
    echo.
    echo Creating virtual environment with Python 3.12...
    py -3.12 -m venv venv312-cuda
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        exit /b 1
    )
)

REM Set explicit paths to venv Python and pip
set VENV_PYTHON=%CD%\venv312-cuda\Scripts\python.exe
set VENV_PIP=%CD%\venv312-cuda\Scripts\pip.exe
set VENV_PYINSTALLER=%CD%\venv312-cuda\Scripts\pyinstaller.exe

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

REM Install CUDA-enabled torch
echo.
echo Installing torch with CUDA support...
"%VENV_PIP%" install torch --index-url https://download.pytorch.org/whl/cu124

echo.
echo ========================================
echo Installing runtime dependencies
echo ========================================

REM Kokoro has complex dependencies with version conflicts
REM Install kokoro 0.7.9 with --no-deps
"%VENV_PIP%" install pillow scipy numpy
"%VENV_PIP%" install "kokoro==0.7.9" --no-deps
"%VENV_PIP%" install loguru huggingface-hub "misaki>=0.7.9,<0.8.0" transformers num2words phonemizer espeakng-loader
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
if exist "dist-cuda" (
    echo Cleaning previous CUDA build...
    rmdir /s /q dist-cuda
)
if exist "build" (
    rmdir /s /q build
)

REM Run PyInstaller with CUDA spec
"%VENV_PYTHON%" -m PyInstaller autiobooks-cuda.spec --clean --distpath dist-cuda --workpath build-cuda

if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller failed.
    exit /b 1
)

REM Replace bundled misaki/en.py with our patched version (makes spacy optional)
if exist "%PROJECT_ROOT%\autiobooks\misaki\en.py" (
    echo Replacing bundled misaki with patched version...
    copy /Y "%PROJECT_ROOT%\autiobooks\misaki\en.py" "dist-cuda\autiobooks-cuda\_internal\misaki\en.py"
)

REM Copy ffmpeg into the dist folder
if exist "ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe" (
    echo.
    echo ========================================
    echo Copying ffmpeg to distribution
    echo ========================================
    copy ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe dist-cuda\autiobooks-cuda\ffmpeg.exe
    copy ffmpeg-master-latest-win64-gpl\bin\ffplay.exe dist-cuda\autiobooks-cuda\ffplay.exe
    copy ffmpeg-master-latest-win64-gpl\bin\ffprobe.exe dist-cuda\autiobooks-cuda\ffprobe.exe
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
    copy %ESPEAK_SRC% dist-cuda\autiobooks-cuda\espeak-ng.exe
    echo espeak-ng copied
) else (
    echo WARNING: espeak-ng.exe not found
)

echo.
echo ========================================
echo Build Summary
echo ========================================
echo.
if exist "dist-cuda\autiobooks-cuda\autiobooks-cuda.exe" (
    echo Executable: dist-cuda\autiobooks-cuda\autiobooks-cuda.exe
    for %%A in ("dist-cuda\autiobooks-cuda\autiobooks-cuda.exe") do echo Size: %%~zA bytes
)
if exist "dist-cuda\autiobooks-cuda\ffmpeg.exe" (
    echo FFmpeg: dist-cuda\autiobooks-cuda\ffmpeg.exe
    for %%A in ("dist-cuda\autiobooks-cuda\ffmpeg.exe") do echo Size: %%~zA bytes
)
echo.
echo To run: dist-cuda\autiobooks-cuda\autiobooks-cuda.exe
echo.
