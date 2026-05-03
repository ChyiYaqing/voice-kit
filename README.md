# AI Voice Assistant — AIY Voice Kit V1

Raspberry Pi 3B + Google AIY Voice Kit V1 语音助手，支持离线 sherpa-onnx 语音识别、LLM 对话（Ollama 或 DeepSeek API）和本地 Piper 语音合成。

## 硬件

- Raspberry Pi 3B
- AIY Google Voice Kit V1 (Voice HAT 声卡)
  - 按键: GPIO 23
  - LED: GPIO 25
  - 麦克风 + 扬声器 via ALSA 设备 `plughw:sndrpigooglevoi,0`

## 架构

```
[按住按键] → arecord → sherpa-onnx STT → 实时工具注入 → LLM (流式) → Piper → aplay → [听到回复]
                                      (时间/天气)    ↓ 逐句合成，三级流水线并行播放
```

| 组件 | 工具 | 说明 |
|------|------|------|
| STT | [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) `streaming-zipformer-bilingual-zh-en` | 离线，~190 MB，Pi 3B 可运行，中英双语，词级识别 |
| 工具 | `tools.py` 实时注入 | 时间、天气查询，无需额外 LLM 调用 |
| LLM | **Ollama**、**DeepSeek API** 或 **Anthropic Claude** | 三种方式可选（见配置） |
| TTS | **edge-tts** `zh-CN-XiaoxiaoNeural` | 微软神经网络 TTS（最自然）；Piper 离线 / espeak-ng 可作 fallback |
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

**选项 3: Anthropic Claude（云服务）**
- 官方 API：https://api.anthropic.com
- 默认模型：`claude-sonnet-4-6`
- 认证方式（优先级从高到低）：
  1. **OAuth Token**（推荐）：通过 Claude.ai 账号授权，token 存于 `credentials/auth-profiles.json`（已 gitignore）
  2. **API Key**（备选）：从 console.anthropic.com 获取 `sk-ant-api03-...`
- 无需额外安装 SDK（直接使用 `requests`）

### TTS 引擎

| 引擎 | 效果 | 是否需要网络 | 配置 |
|------|------|------------|------|
| `edge`（默认）| 最佳，微软神经 TTS | 是 | `TTS_VOICE`, `TTS_RATE`, `TTS_VOLUME` |
| `piper` | 较好，离线 ONNX | 否 | `PIPER_MODEL`, `PIPER_VOLUME` |
| `espeak` | 基础 | 否 | `LOCAL_TTS_VOICE`, `LOCAL_TTS_SPEED` |

edge-tts 流程：`edge-tts → MP3 → ffmpeg → 16kHz WAV → aplay`

### TTS 三级流水线

```
Thread-1 (LLM 读取): LLM 流式输出 → 分句边界检测 → sentence_q
Thread-2 (合成器):   sentence_q → TTS WAV → wav_q (合成下一句)
主线程 (播放器):     wav_q → aplay            (播放当前句)
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

1. **并发录音+识别**：arecord 实时输出 PCM，sherpa-onnx 边录边转写，按钮松开时识别已完成大部分
2. **实时工具注入**：识别时间/天气意图后，在 LLM 调用前注入 `[实时数据]` 上下文块，无额外 LLM 调用
3. **流式 LLM 响应**：首句在 ~2-3 秒内开始播放，无需等待完整回答生成
4. **三级 TTS 流水线**：播放当前句的同时合成下一句，消除句间停顿
5. **多轮对话**：内存中维护会话历史，支持上下文连贯对话
6. **三 LLM 支持**：灵活切换 Ollama（自托管）、DeepSeek API 或 Anthropic Claude
7. **系统提示优化**：强制 1-3 句简短回复，禁用 markdown，适配语音场景

## 实时工具注入

`tools.py` 在将用户文本送入 LLM 之前，检测意图并注入实时数据——无需额外 LLM 调用：

| 工具 | 触发关键词 | 数据来源 |
|------|----------|---------|
| 当前时间 | `几点` `时间` `今天几号` `星期几` `what time` | 系统时钟 |
| 天气查询 | `天气` `下雨` `温度` `气温` `冷不冷` `weather` | `wttr.in`（无需 API key） |

**工作原理：**

```
用户: "现在几点了？" / "上海今天天气怎么样？"
         ↓ tools.enrich_query()
[实时数据]
当前时间: 2026年03月29日 星期日 14:30:00
天气信息: 上海: ⛅ +18°C

用户问题: 上海今天天气怎么样？
         ↓ LLM 收到完整上下文，直接回答
```

**城市配置**（优先级从高到低）：
1. `memory/USER.md` 中 `城市: 上海` 或 `city: Shanghai` 格式的行（自动解析）
2. `.env` 中的 `USER_CITY=上海`

**天气 API**：`wttr.in/{city}?format=3&lang=zh`，无需注册，超时 5 秒（可配置）

**配置示例（.env）：**
```bash
USER_CITY=上海          # 天气查询的默认城市
WEATHER_TIMEOUT=5       # 天气 API 超时秒数
```

## 持久化记忆系统

受 [OpenClaw](https://github.com/openclaw/openclaw) 启发，本项目集成了 Bootstrap 注入系统，实现助手人格、用户画像和长期记忆的跨重启保留。

### Bootstrap 文件系统（OpenClaw-inspired）

**四层 Bootstrap 架构：**

1. **SOUL.md** - 助手的灵魂（核心人格和行为哲学）
   - 定义对话风格、价值观和行为边界
   - 替代硬编码的 System Prompt
   - **可演化**：助手可以自我调整来更好服务用户
   - 手动编辑以自定义助手人格

2. **IDENTITY.md** - 助手的身份（自我认知）
   - 名字、角色定义、个性标签
   - 能力说明和限制
   - 首次启动时自动创建

3. **USER.md** - 用户画像（你是谁）
   - 用户姓名、称呼偏好、沟通风格
   - 兴趣、项目背景、技术水平
   - 从对话中逐渐积累

4. **MEMORY.md** - 长期记忆（学到的重要事情）
   - 用户偏好、行为规则、学习到的模式
   - < 100 行建议（保持聚焦）
   - 由助手自动更新

5. **history.jsonl** - 完整对话历史
   - JSONL 格式（每行一条消息，易于追加和轮转）
   - 每次对话后自动保存
   - 启动时加载最近 50 条消息（可配置）
   - 达到阈值时自动轮转备份（默认 2000 行）

### Bootstrap 注入机制

每次对话时，System Prompt 按以下顺序构建：

```
┌─────────────────────────────────────┐
│ 1. SOUL.md (你的灵魂)               │ ← 核心人格，如不存在则使用默认
├─────────────────────────────────────┤
│ 2. IDENTITY.md (你的身份)           │ ← 自我认知
├─────────────────────────────────────┤
│ 3. USER.md (你在帮助的人)           │ ← 用户画像
├─────────────────────────────────────┤
│ 4. MEMORY.md (长期记忆)             │ ← 学到的重要信息
├─────────────────────────────────────┤
│ 5. 文件更新协议                     │ ← 演化机制
└─────────────────────────────────────┘
```

**核心理念**（来自 OpenClaw）：
> "每次启动你都以全新状态唤醒。这些文件就是你的记忆。读它们，更新它们，它们是你持续存在的方式。"

### 目录结构

```
voice-kit/
└── memory/                      # Bootstrap + 记忆存储目录（可配置）
    ├── SOUL.md                  # 助手人格（可手动编辑）
    ├── IDENTITY.md              # 助手身份（可手动编辑）
    ├── USER.md                  # 用户画像（助手自动积累）
    ├── MEMORY.md                # 长期记忆（助手自动更新）
    ├── history.jsonl            # 对话历史
    └── history.jsonl.backup     # 轮转备份
```

### 配置选项

在 `.env` 文件中配置：

```bash
# ── 记忆系统 ──────────────────────────────────────────────
# 启用持久化记忆（默认: true）
MEMORY_ENABLED=true

# 记忆存储目录（默认: ./memory）
MEMORY_DIR=./memory

# 启动时加载的最大消息数（默认: 50）
MAX_HISTORY_MESSAGES=50

# 历史文件轮转阈值，单位：行（默认: 2000）
HISTORY_ROTATION_THRESHOLD=2000

# ── Bootstrap 系统 ────────────────────────────────────────
# 启用 Bootstrap 注入（SOUL.md, IDENTITY.md, USER.md）
BOOTSTRAP_ENABLED=true

# 单个 Bootstrap 文件最大字符数（默认: 20000）
BOOTSTRAP_MAX_CHARS=20000

# 所有 Bootstrap 文件总字符数上限（默认: 150000）
BOOTSTRAP_TOTAL_MAX_CHARS=150000

# ── 自动更新标记（高级用户）──────────────────────────────
MEMORY_UPDATE_MARKER=[UPDATE_MEMORY]
USER_UPDATE_MARKER=[UPDATE_USER]
IDENTITY_UPDATE_MARKER=[UPDATE_IDENTITY]
SOUL_UPDATE_MARKER=[UPDATE_SOUL]
```

### 自动演化机制

助手会在对话中学习并自动更新 Bootstrap 文件：

**示例对话 1：学习用户偏好**
```
用户：我喜欢简短的回答，不要啰嗦
助手：好的，我会保持简洁。
     [系统自动记录到 MEMORY.md: 用户偏好简短回答]
```

**示例对话 2：更新用户画像**
```
用户：我在做树莓派项目，需要用 Python 控制 GPIO
助手：明白了，我会重点帮你解决 GPIO 相关问题。
     [系统自动记录到 USER.md: 用户正在做树莓派项目]
```

**示例对话 3：调整身份认知**
```
用户：你可以叫我老王
助手：好的老王，以后我这样称呼你。
     [系统自动记录到 USER.md: 用户偏好称呼"老王"]
```

**Bootstrap 文件更新示例**：

MEMORY.md 自动添加：
```markdown
## Recent Learnings
- [2026-03-18 10:45] 用户偏好简短、直接的回答
```

USER.md 自动添加：
```markdown
## 兴趣和上下文
- [2026-03-18 10:50] 用户正在做树莓派项目，使用 Python 控制 GPIO
```

### 手动编辑 Bootstrap 文件

所有 Bootstrap 文件都可以手动编辑来自定义助手行为：

```bash
# 编辑助手人格
nano memory/SOUL.md

# 编辑助手身份
nano memory/IDENTITY.md

# 编辑用户画像
nano memory/USER.md

# 编辑长期记忆
nano memory/MEMORY.md
```

修改后重启助手立即生效。

**推荐编辑场景**：
- SOUL.md: 调整对话风格（更幽默/更正式/更简洁）
- IDENTITY.md: 给助手起名字、定义个性
- USER.md: 添加重要上下文（职业、兴趣、常用设备）
- MEMORY.md: 整理重要偏好和规则

### 清除记忆

如需重新开始（清除所有记忆）：

```bash
rm -rf memory/
# 下次启动时会自动创建空白记忆模板
```

或仅清除对话历史，保留 MEMORY.md：

```bash
rm memory/history.jsonl*
```

### 性能影响

- **启动延迟：**<20ms（加载 MEMORY.md + 50 条历史消息）
- **每轮对话开销：**<3ms（保存消息到磁盘）
- **内存占用增加：**+18 KB（50 条消息 + MEMORY.md）
- **磁盘占用：**约 300 KB（2000 行历史记录）

对 Pi 3B（1GB RAM）几乎没有性能影响。

### 故障恢复

记忆系统采用优雅降级策略：
- **文件缺失：**自动创建默认模板
- **文件损坏：**跳过损坏行，加载有效数据
- **磁盘空间不足：**禁用保存，继续内存运行
- **权限错误：**记录警告，关闭持久化功能

任何错误都不会导致助手崩溃，始终优先保证正常运行。

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

- **当前**：`sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20`（中英双语，int8，encoder ~174 MB，总计 ~190 MB）
- **解码**：`modified_beam_search`（`max_active_paths=4`）— 比 `greedy_search` 在快速重复音节（如「测试测试测试」）上更稳定，CPU 成本约 +30%
- **优势**：词级 token 输出，支持中英混合识别（如「苹果 iPhone」）；不会把复合词切成独立字符
- **注意**：Pi 3B (1 GB RAM) 模型加载约需 75s；避免使用超过 200 MB 的模型

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
- 安装依赖：`sherpa-onnx`, `numpy`, `requests`, `gpiozero`, `python-dotenv`
- 下载 sherpa-onnx 中英双语模型（int8，~190 MB）
- 链接系统 `python3-rpi-lgpio` 到 venv（Linux 6.12+ 兼容）
- 安装 ffmpeg（TTS 音频转换需要）

**手动下载模型（若 setup.sh 网络失败）：**

```bash
mkdir -p sherpa-model && cd sherpa-model
BASE="https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20/resolve/main"
for f in encoder-epoch-99-avg-1.int8.onnx decoder-epoch-99-avg-1.int8.onnx joiner-epoch-99-avg-1.int8.onnx bpe.model tokens.txt; do
  wget -c "$BASE/$f"
done
```

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

#### 使用 Anthropic Claude

**方式一：OAuth Token（推荐）**

```bash
# 1. 设置提供商
export LLM_PROVIDER=claude

# 2. 创建凭证文件（gitignored，勿提交）
cp credentials/auth-profiles.json.example credentials/auth-profiles.json
# 编辑 credentials/auth-profiles.json，填入 OAuth token（sk-ant-oau04-...）
```

凭证文件格式（`credentials/auth-profiles.json`）：
```json
{
  "anthropic:claude-cli": {
    "type": "oauth",
    "provider": "anthropic",
    "token": "sk-ant-oau04-YOUR_TOKEN"
  }
}
```

**方式二：API Key（备选）**

```bash
export LLM_PROVIDER=claude
export ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxxxxx
```

```bash
export ANTHROPIC_MODEL=claude-sonnet-4-6  # 可选，默认 claude-sonnet-4-6
export ANTHROPIC_MAX_TOKENS=1024          # 可选，默认 1024
export ANTHROPIC_TIMEOUT=60               # 可选，默认 60 秒
```

### TTS 语音配置

通过环境变量或 `config.py` 调整：

```bash
export LOCAL_TTS_VOICE="cmn"        # 普通话；也可试 zh、yue
export LOCAL_TTS_SPEED=175          # 语速，数值越大越快
export LOCAL_TTS_AMPLITUDE=100      # 音量，0-200
export PIPER_VOLUME=0.2             # Piper 输出音量，0.2 约等于 20%
```

Piper 不依赖网络，中文音质比 espeak-ng 自然。Pi 3B 上启动时会预加载模型，后续短句通常在 1-2 秒内合成。

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

### LLM 连接失败 / DeepSeek 400 错误

**DeepSeek 400 Bad Request 常见原因**：
- API Key 失效或余额不足
- `DEEPSEEK_MODEL` 名称不正确（应为 `deepseek-chat`）
- 消息历史中存在空 `content` 字段

**自动 Fallback 机制**：当 DeepSeek 返回任意错误时，服务会自动切换到 Ollama 继续响应，不崩溃。错误详情会打印到日志：
```bash
journalctl -u voice-assistant -f -o short-precise
# 示例：[DeepSeek] HTTP 400: {"error": ...}
#        [LLM] Falling back to Ollama
```

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
├── assistant.py              # 主程序（录音→STT→工具→LLM→TTS→播放）
├── config.py                 # 配置文件（读取 .env）
├── tools.py                  # 实时工具注入（时间、天气）
├── memory_store.py           # Bootstrap + 持久化记忆存储
├── setup.sh                  # 一键安装脚本
├── voice-assistant.service   # systemd 服务单元
├── .env.example              # 配置模板
├── .env                      # 实际配置（不提交到 git）
├── sherpa-model/             # sherpa-onnx 中英双语模型（~190 MB，不提交）
├── memory/                   # Bootstrap 文件 + 对话历史（不提交）
└── .venv/                    # Python 虚拟环境
```

## 许可证

MIT License

## 致谢

- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) - 离线流式语音识别
- [Ollama](https://ollama.ai/) - 本地 LLM 部署
- [DeepSeek](https://www.deepseek.com/) - DeepSeek API
- [Piper](https://github.com/rhasspy/piper) - 本地神经 TTS
- [espeak-ng](https://github.com/espeak-ng/espeak-ng) - 本地离线 TTS fallback
- Google AIY Voice Kit V1 - 硬件平台
