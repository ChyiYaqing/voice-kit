# AI Voice Assistant — AIY Voice Kit V1

Raspberry Pi 3B + Google AIY Voice Kit V1 语音助手，支持离线 Vosk 语音识别、LLM 对话（Ollama 或 DeepSeek API）和 edge-tts 中文女声合成。

## 硬件

- Raspberry Pi 3B
- AIY Google Voice Kit V1 (Voice HAT 声卡)
  - 按键: GPIO 23
  - LED: GPIO 25
  - 麦克风 + 扬声器 via ALSA 设备 `plughw:sndrpigooglevoi,0`

## 架构

```
[按住按键] → arecord → Vosk STT → LLM (流式) → edge-tts+ffmpeg → aplay → [听到回复]
                                      ↓ 逐句合成，三级流水线并行播放
```

| 组件 | 工具 | 说明 |
|------|------|------|
| STT | [Vosk](https://alphacephei.com/vosk/) `small-cn-0.22` | 离线，~42 MB，Pi 3B 可运行，普通话 |
| LLM | **Ollama** 或 **DeepSeek API** | 两种方式可选（见配置） |
| TTS | edge-tts `zh-CN-XiaoxiaoNeural` | 微软神经语音，中文女声，需联网 |
| GPIO | gpiozero + lgpio | 按住录音，LED 状态指示（兼容 Linux 6.12+） |

### LLM 提供商

**选项 1: Ollama（本地或远程部署）**
- 支持模型：`gemma3:4b`, `gemma3:12b`, `deepseek-r1:7b`, `qwen3:latest`
- 适合本地 Mac Mini M4 或其他自托管服务器
- 使用 HTTP Basic 认证

**选项 2: DeepSeek API（云服务）**
- 官方 API：https://api.deepseek.com
- 默认模型：`deepseek-chat`
- 使用 API Key 认证

### TTS 三级流水线

```
Thread-1 (LLM 读取): LLM 流式输出 → 分句边界检测 → sentence_q
Thread-2 (合成器):   sentence_q → edge-tts+ffmpeg → wav_q  (合成下一句)
主线程 (播放器):     wav_q → aplay                          (播放当前句)
```

**工作原理：**
- 播放句子 N 的同时，句子 N+1 已在后台合成，**消除句间停顿**
- 分句边界：中英文标点 `。！？!?.` 和逗号 `，`（后接 20+ 字符时）
- Markdown 字符自动过滤：`* # `` _ ~ > [ ] ( )`
- 首句 TTS 延迟：~2-3 秒（LLM 流式输出 + 首句合成）

## LED 状态

| 状态 | LED |
|------|-----|
| 待机就绪 | 双闪后熄灭 |
| 录音中 | 常亮 |
| 识别 / 查询中 | 快速闪烁 |
| 播放回复 | 慢速闪烁 |

## 技术特性

✨ **关键优化：**

1. **并发录音+识别**：arecord 实时输出 PCM，Vosk 边录边转写，按钮松开时识别已完成大部分
2. **流式 LLM 响应**：首句在 ~2-3 秒内开始播放，无需等待完整回答生成
3. **三级 TTS 流水线**：播放当前句的同时合成下一句，消除句间停顿
4. **多轮对话**：内存中维护会话历史，支持上下文连贯对话
5. **双 LLM 支持**：灵活切换 Ollama（自托管）或 DeepSeek API（云服务）
6. **系统提示优化**：强制 1-3 句简短回复，禁用 markdown，适配语音场景

## 依赖说明

### GPIO 驱动 (Linux 6.12+ 兼容性)

Linux 6.12+ 内核破坏了 pip 安装的 `RPi.GPIO` 0.7.1 边沿检测接口。本项目使用解决方案：

1. **系统包**：使用 `python3-rpi-lgpio`（RPi.GPIO 0.7.2，基于 lgpio）
2. **符号链接**：通过 `setup.sh` 将系统包链接到 venv（避免 pip 安装损坏的版本）
3. **环境变量**：systemd 服务中设置 `GPIOZERO_PIN_FACTORY=lgpio`

```bash
# 手动运行时也需要设置
export GPIOZERO_PIN_FACTORY=lgpio
python assistant.py
```

### Python 输出缓冲

systemd 服务下 Python stdout 默认缓冲，导致日志延迟。服务文件中设置 `PYTHONUNBUFFERED=1` 解决，使日志实时显示在 journalctl。

### STT 语言模型

- **当前**：`vosk-model-small-cn-0.22`（普通话，~42 MB）
- **备份**：`vosk-model-en-backup/`（英文，如需切换可重命名目录）
- **注意**：Pi 3B (1 GB RAM) 不支持 large 模型

### 音频设备

- **声卡**：AIY Voice Kit V1 ALSA 设备名 `sndrpigooglevoi`
- **录音/播放**：使用 `arecord`/`aplay` + `plughw:sndrpigooglevoi,0`（不使用 sounddevice/PortAudio）
- **原因**：避免 `/etc/asound.conf` 中默认路由的冲突问题

## 安装

**一键安装脚本：**

```bash
bash setup.sh
```

脚本会自动完成：
- 创建 Python 3.13 venv (`.venv/`)
- 安装依赖：`vosk`, `requests`, `gpiozero`, `edge-tts`, `python-dotenv`
- 下载 Vosk 中文模型 `small-cn-0.22`（~42 MB）
- 链接系统 `python3-rpi-lgpio` 到 venv（Linux 6.12+ 兼容）
- 安装 ffmpeg（TTS 音频转换需要）

**手动确认系统依赖：**

```bash
# Debian Bookworm 通常已包含
sudo apt install python3-rpi-lgpio ffmpeg
```

## 运行

### 手动运行

```bash
source .venv/bin/activate

# Linux 6.12+ 需要设置 GPIO 工厂
export GPIOZERO_PIN_FACTORY=lgpio

# 启动助手
python assistant.py
```

### systemd 服务（推荐，开机自启）

```bash
# 启动服务
sudo systemctl start voice-assistant

# 查看状态
sudo systemctl status voice-assistant

# 实时日志
journalctl -u voice-assistant -f -o short-precise
```

服务文件已自动配置 `GPIOZERO_PIN_FACTORY=lgpio` 和 `PYTHONUNBUFFERED=1`。

## 配置

### LLM 提供商配置

**方式一：使用 `.env` 文件（推荐）**

```bash
cp .env.example .env
# 编辑 .env 文件，根据选择的 LLM 提供商填写对应配置
```

**方式二：环境变量**

#### 使用 Ollama（本地/远程）

```bash
# 设置提供商为 Ollama
export LLM_PROVIDER=ollama

# Ollama 服务器配置
export OLLAMA_HOST=https://your-ollama-server.com
export OLLAMA_MODEL=gemma3:4b        # 可选: gemma3:12b, deepseek-r1:7b, qwen3:latest
export OLLAMA_USERNAME=your_username
export OLLAMA_PASSWORD=your_password
export OLLAMA_TIMEOUT=60             # 可选，默认 60 秒
```

#### 使用 DeepSeek API

```bash
# 设置提供商为 DeepSeek
export LLM_PROVIDER=deepseek

# DeepSeek API 配置
export DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx
export DEEPSEEK_MODEL=deepseek-chat  # 可选，默认 deepseek-chat
export DEEPSEEK_HOST=https://api.deepseek.com  # 可选
export DEEPSEEK_TIMEOUT=60           # 可选，默认 60 秒
```

### TTS 语音配置

通过环境变量或 `config.py` 调整：

```bash
export TTS_VOICE="zh-CN-XiaoxiaoNeural"  # 语音选择
export TTS_RATE="+0%"                     # 语速："+10%" 加速，"-10%" 减速
export TTS_VOLUME="-90%"                  # 音量："-70%" 降低
```

**其他可用中文女声：**
- `zh-CN-XiaoxiaoNeural` （晓晓，默认）
- `zh-CN-XiaochenNeural` （晓辰）
- `zh-CN-XiaohanNeural`  （晓涵）

## Systemd 服务管理

```bash
# 启用开机自启
sudo systemctl enable voice-assistant

# 服务控制
sudo systemctl start voice-assistant
sudo systemctl stop voice-assistant
sudo systemctl restart voice-assistant

# 查看状态和日志
sudo systemctl status voice-assistant
journalctl -u voice-assistant -f -o short-precise
```

**服务配置文件**：`/etc/systemd/system/voice-assistant.service`
**运行用户**：当前用户
**环境变量**：自动设置 `GPIOZERO_PIN_FACTORY=lgpio` 和 `PYTHONUNBUFFERED=1`

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/ChyiYaqing/voice-kit.git
cd voice-kit

# 2. 运行安装脚本
bash setup.sh

# 3. 配置 LLM（选择一种）
cp .env.example .env
nano .env  # 填写 Ollama 或 DeepSeek 配置

# 4. 测试运行
source .venv/bin/activate
export GPIOZERO_PIN_FACTORY=lgpio
python assistant.py

# 5. 设置开机自启（可选）
sudo systemctl enable voice-assistant
sudo systemctl start voice-assistant
```

## 故障排除

### GPIO 错误：`RuntimeError: Cannot determine pin`

**原因**：未设置 lgpio 工厂或未安装 `python3-rpi-lgpio`

**解决**：
```bash
# 安装系统包
sudo apt install python3-rpi-lgpio

# 设置环境变量
export GPIOZERO_PIN_FACTORY=lgpio

# 或在代码前添加（不推荐，应使用环境变量）
# import os
# os.environ['GPIOZERO_PIN_FACTORY'] = 'lgpio'
```

### LLM 连接失败

**检查步骤**：
1. 确认 `.env` 文件存在且配置正确
2. 测试 Ollama/DeepSeek 连接：
   ```bash
   # Ollama
   curl -u username:password https://your-ollama-server.com/api/tags

   # DeepSeek
   curl -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
        https://api.deepseek.com/models
   ```
3. 检查防火墙和网络连接

### 音频设备未找到

**检查 ALSA 设备**：
```bash
aplay -l  # 查看播放设备
arecord -l  # 查看录音设备
# 应该看到 "sndrpigooglevoi" 声卡
```

如果设备不存在，检查 AIY Voice HAT 是否正确连接。

### 日志不实时显示

**手动运行**：确保设置 `PYTHONUNBUFFERED=1`
```bash
export PYTHONUNBUFFERED=1
python -u assistant.py
```

**systemd 服务**：服务文件已包含此设置，无需额外配置。

## 项目结构

```
voice-kit/
├── assistant.py              # 主程序（录音→STT→LLM→TTS→播放）
├── config.py                 # 配置文件（读取 .env）
├── setup.sh                  # 一键安装脚本
├── voice-assistant.service   # systemd 服务单元
├── .env.example              # 配置模板
├── .env                      # 实际配置（不提交到 git）
├── vosk-model/               # Vosk 中文模型（~42 MB，不提交）
├── vosk-model-en-backup/     # 英文模型备份（可选）
└── .venv/                    # Python 虚拟环境
```

## 许可证

MIT License

## 致谢

- [Vosk](https://alphacephei.com/vosk/) - 离线语音识别
- [Ollama](https://ollama.ai/) - 本地 LLM 部署
- [DeepSeek](https://www.deepseek.com/) - DeepSeek API
- [edge-tts](https://github.com/rany2/edge-tts) - 微软 TTS 接口
- Google AIY Voice Kit V1 - 硬件平台
