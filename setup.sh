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
    sherpa-onnx \
    numpy \
    requests \
    gpiozero \
    RPi.GPIO \
    piper-tts==1.3.0 \
    onnxruntime==1.23.2

echo "=== 3. Download sherpa-onnx streaming-zipformer-zh-14M (~25 MB) ==="
MODEL_DIR="$SCRIPT_DIR/sherpa-model"
if [ ! -d "$MODEL_DIR" ]; then
    TMP=$(mktemp -d)
    wget -q --show-progress \
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23.tar.bz2" \
        -O "$TMP/model.tar.bz2"
    tar xf "$TMP/model.tar.bz2" -C "$TMP"
    mv "$TMP/sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23" "$MODEL_DIR"
    rm -rf "$TMP"
    echo "sherpa-onnx model saved to $MODEL_DIR"
else
    echo "sherpa-onnx model already present — skipping."
fi

echo "=== 4. Download Piper Chinese voice (~21 MB) ==="
PIPER_DIR="$SCRIPT_DIR/models/piper"
mkdir -p "$PIPER_DIR"
if [ ! -f "$PIPER_DIR/zh_CN-huayan-x_low.onnx" ]; then
    wget -q --show-progress \
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/x_low/zh_CN-huayan-x_low.onnx" \
        -O "$PIPER_DIR/zh_CN-huayan-x_low.onnx"
else
    echo "Piper ONNX model already present — skipping."
fi
if [ ! -f "$PIPER_DIR/zh_CN-huayan-x_low.onnx.json" ]; then
    wget -q --show-progress \
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/x_low/zh_CN-huayan-x_low.onnx.json" \
        -O "$PIPER_DIR/zh_CN-huayan-x_low.onnx.json"
else
    echo "Piper config already present — skipping."
fi

echo ""
echo "=== Setup complete ==="
echo "Activate venv:  source $SCRIPT_DIR/.venv/bin/activate"
echo "Run assistant:  python $SCRIPT_DIR/assistant.py"
echo ""
echo "Optional: edit OLLAMA_HOST / OLLAMA_MODEL in config.py"
echo "          or export them as environment variables before running."
