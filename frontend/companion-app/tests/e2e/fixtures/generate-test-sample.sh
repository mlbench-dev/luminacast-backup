#!/bin/bash
OUT="$(dirname "$0")/test-voice-sample.mp4"
if [ -f "$OUT" ]; then echo "Already exists: $OUT"; exit 0; fi

TEXT="Hi everyone welcome back to my channel. Today I am so excited to show you this amazing product. It is literally one of my favorites, you have to try it. The quality is insane."
echo "$TEXT" | espeak-ng -w /tmp/test_speech.wav 2>/dev/null || \
echo "$TEXT" | espeak -w /tmp/test_speech.wav 2>/dev/null || \
{ echo "ERROR: espeak not found"; exit 1; }

ffmpeg -y -f lavfi -i "color=c=0x1a1a2e:s=720x1280:d=15" \
  -i /tmp/test_speech.wav \
  -c:v libx264 -preset ultrafast -tune stillimage \
  -c:a aac -b:a 128k -pix_fmt yuv420p \
  -shortest -movflags +faststart "$OUT"
echo "Created: $OUT"
