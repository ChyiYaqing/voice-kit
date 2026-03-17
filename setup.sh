#!/usr/bin/env bash
# Setup script for AI Voice Assistant — AIY Voice Kit V1
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== 1. System packages ==="
sudo apt-get update -qq
sudo apt-get install -y \
    espeak-ng \
    alsa-utils \
    wget unzip \
    python3-pip \
    python3-venv

echo "=== 2. Python virtual environment ==="
python3 -m venv "$SCRIPT_DIR/.venv"
source "$SCRIPT_DIR/.venv/bin/activate"

pip install --upgrade pip -q
pip install -q \
    vosk \
    requests \
    gpiozero \
    RPi.GPIO

echo "=== 3. Download Vosk small English model (~40 MB) ==="
MODEL_DIR="$SCRIPT_DIR/vosk-model"
if [ ! -d "$MODEL_DIR" ]; then
    TMP=$(mktemp -d)
    wget -q --show-progress \
        "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip" \
        -O "$TMP/model.zip"
    unzip -q "$TMP/model.zip" -d "$TMP"
    mv "$TMP"/vosk-model-small-en-us-0.15 "$MODEL_DIR"
    rm -rf "$TMP"
    echo "Vosk model saved to $MODEL_DIR"
else
    echo "Vosk model already present — skipping."
fi

echo ""
echo "=== Setup complete ==="
echo "Activate venv:  source $SCRIPT_DIR/.venv/bin/activate"
echo "Run assistant:  python $SCRIPT_DIR/assistant.py"
echo ""
echo "Optional: edit OLLAMA_HOST / OLLAMA_MODEL in config.py"
echo "          or export them as environment variables before running."
