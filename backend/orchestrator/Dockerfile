FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    libpq-dev \
    gcc \
    g++ \
    python3-dev \
    wget \
    libgl1 \
    libglib2.0-0 \
    libgles2 \
    libegl1 \
    libgbm1 \
    fonts-noto-color-emoji \
    fonts-dejavu-core \
    unzip \
    fontconfig \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir yt-dlp

# Caption Fix 3 Phase A — bundle the Inter font family at the path the
# FFmpeg composer expects (/usr/share/fonts/truetype/inter/Inter-*.ttf).
# Same family + weights as the editor preview so preview ≈ render.
RUN mkdir -p /usr/share/fonts/truetype/inter && \
    wget -q -O /tmp/inter.zip "https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip" && \
    unzip -q /tmp/inter.zip -d /tmp/inter && \
    cp /tmp/inter/extras/ttf/Inter-Light.ttf \
       /tmp/inter/extras/ttf/Inter-Regular.ttf \
       /tmp/inter/extras/ttf/Inter-Medium.ttf \
       /tmp/inter/extras/ttf/Inter-SemiBold.ttf \
       /tmp/inter/extras/ttf/Inter-Bold.ttf \
       /tmp/inter/extras/ttf/Inter-ExtraBold.ttf \
       /tmp/inter/extras/ttf/Inter-Black.ttf \
       /usr/share/fonts/truetype/inter/ && \
    fc-cache -fv && \
    rm -rf /tmp/inter /tmp/inter.zip

# Caption preset fonts — the preview uses these via @fontsource, the
# FFmpeg drawtext filter resolves them through FONT_FILE_MAP in
# cast_ffmpeg_composer.py. Pulling each TTF directly from the Google
# Fonts repo so the burnt-in MP4 matches the canvas preview for all 15
# presets (Hormozi, Pill, Karaoke, Minimal, Neon, Typewriter, Block
# Quote, ALL CAPS, Handwritten, Gradient, Box, Outline, TikTok, Split,
# Comic).
RUN mkdir -p /usr/share/fonts/truetype/custom && \
    cd /usr/share/fonts/truetype/custom && \
    wget -q "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf" -O Montserrat.ttf && \
    wget -q "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf" -O Anton.ttf && \
    wget -q "https://github.com/google/fonts/raw/main/ofl/bebasneue/BebasNeue-Regular.ttf" -O BebasNeue.ttf && \
    wget -q "https://github.com/google/fonts/raw/main/ofl/caveat/Caveat%5Bwght%5D.ttf" -O Caveat.ttf && \
    wget -q "https://github.com/google/fonts/raw/main/ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf" -O PlayfairDisplay.ttf && \
    wget -q "https://github.com/google/fonts/raw/main/ofl/bangers/Bangers-Regular.ttf" -O Bangers.ttf && \
    wget -q "https://github.com/google/fonts/raw/main/ofl/comicneue/ComicNeue-Bold.ttf" -O ComicNeue-Bold.ttf && \
    wget -q "https://github.com/google/fonts/raw/main/ofl/indieflower/IndieFlower-Regular.ttf" -O IndieFlower.ttf && \
    fc-cache -fv

# Install CPU-only PyTorch stack FIRST (torch, torchaudio, torchcodec, torchvision)
# All must come from CPU index to avoid CUDA library mismatches.
# pyannote-audio 4.x needs torchcodec; audio-separator needs torchvision.
RUN pip install --no-cache-dir torch torchaudio torchcodec torchvision --index-url https://download.pytorch.org/whl/cpu

# Install Python dependencies (pyannote-audio will find torch/torchcodec already installed)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install audio-separator with CPU PyTorch for local BS-RoFormer vocal isolation fallback
RUN pip install --no-cache-dir 'audio-separator[cpu]'

# Copy application code
COPY . .

# Create stream cache directory
RUN mkdir -p /tmp/stream_cache

# Download mediapipe models (B-075 face detector + Session E pose landmarker)
RUN mkdir -p /app/models && \
    wget -q -O /app/models/blaze_face_short_range.tflite \
    https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite && \
    wget -q -O /app/models/pose_landmarker_full.task \
    https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
