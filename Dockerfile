FROM python:3.12-slim

# System dependencies: ffmpeg, tkinter, X11 libs, audio libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    python3-tk \
    libx11-6 \
    libxext6 \
    libxrender1 \
    libxft2 \
    libxss1 \
    libsndfile1 \
    libsdl2-mixer-2.0-0 \
    libsdl2-2.0-0 \
    espeak-ng \
    libgomp1 \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install heavy Python deps first for layer caching
RUN pip install --no-cache-dir \
    "pillow>=10.0.0" \
    "kokoro>=0.7.9,<0.8.0" \
    "ebooklib>=0.18,<0.19" \
    "soundfile>=0.13.1,<0.14.0" \
    "pygame>=2.0.1,<3.0.0" \
    "bs4>=0.0.2,<0.0.3"

# Copy project and install
COPY pyproject.toml setup.py README.md LICENSE ./
COPY autiobooks/ autiobooks/
RUN pip install --no-cache-dir .

# Config persisted via volume
VOLUME ["/root/.autiobooks"]

# Books input/output directory
VOLUME ["/books"]
WORKDIR /books

ENV DISPLAY=:0
# Use dummy SDL audio driver so pygame.mixer.init() works without a sound device
ENV SDL_AUDIODRIVER=dummy
# Ensure Python output is visible in Docker logs
ENV PYTHONUNBUFFERED=1
# Source uses bare imports (from engine import ...) instead of relative imports
ENV PYTHONPATH="/usr/local/lib/python3.12/site-packages/autiobooks:${PYTHONPATH}"

ENTRYPOINT ["python", "-m", "autiobooks"]
