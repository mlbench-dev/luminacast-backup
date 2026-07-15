FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Install PyTorch CPU
RUN pip install --no-cache-dir \
    torch torchaudio --index-url https://download.pytorch.org/whl/cpu

# Install audio processing stack
RUN pip install --no-cache-dir \
    runpod \
    audio-separator[cpu] \
    openai-whisper \
    boto3 \
    requests \
    numpy \
    scipy

# Pre-download BS-RoFormer model for vocal isolation
RUN python -c "from audio_separator.separator import Separator; s = Separator(); s.load_model('model_bs_roformer_ep_317_sdr_12.9755.ckpt')"

# Pre-download Whisper large-v3 model for transcription (runs on GPU at runtime)
RUN python -c "import whisper; whisper.load_model('large-v3')"

WORKDIR /app
COPY handler.py .

CMD ["python", "-u", "handler.py"]
