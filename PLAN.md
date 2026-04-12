# Web 端开发规划

> Pi 3B 资源约束：1GB RAM、ARM64、无 Node.js 构建工具。
> 技术选型原则：轻量、依赖少、与现有 Python 环境兼容。

---

## 技术栈

| 层次 | 选型 | 理由 |
|------|------|------|
| Web 后端 | FastAPI + uvicorn | 异步、原生 SSE/WebSocket、轻量 |
| 前端 | 原生 HTML + JS（无框架） | 无构建步骤，Pi 上直接运行 |
| 语音输入 | Web Speech API（浏览器原生） | 无需额外依赖，Chrome/Edge 支持 |
| 实时推送 | SSE（Server-Sent Events） | 单向流，比 WebSocket 简单，适合日志和 LLM 流式输出 |
| LLM 调用 | 复用 `assistant.py` 的 `stream_llm()` | 保持逻辑一致，共享 Bootstrap/Memory 上下文 |
| 历史数据 | 读取 `memory/history.jsonl` | 已有持久化，无需新存储 |

---

## 模块拆分

### 新增文件

```
web_server.py          # FastAPI 应用入口
templates/
  index.html           # 单页应用（对话 + 日志 + 历史三栏）
static/
  app.js               # 前端逻辑（Web Speech API、SSE 接收）
  style.css            # 最小化样式
```

### 复用文件（只读，不修改）

```
assistant.py           # 引用 stream_llm()、build_system_prompt()
memory_store.py        # 引用 MemoryStore（读取历史、Bootstrap 文件）
config.py              # 共享配置
tools.py               # 共享工具注入
```

---

## 功能细节

### 1. 前端语音识别 → LLM 文字输出

**流程：**
```
浏览器 Web Speech API
  → 识别出文字
  → POST /api/chat  {"text": "用户说的话"}
  → FastAPI 调用 stream_llm()
  → SSE 流式返回 LLM 回答
  → 前端逐字渲染
```

**接口：**
- `POST /api/chat` — 接收 `{"text": str}`，返回 `text/event-stream`
  - 每个 SSE event 格式：`data: {"chunk": "..."}\n\n`
  - 结束标志：`data: {"done": true}\n\n`

**前端关键点：**
- `SpeechRecognition` 设置 `lang: 'zh-CN'`，支持中英混合
- 按住说话 / 松开发送（与实体按钮体验一致）
- LLM 回答区域流式追加文字，使用 `EventSource`

---

### 2. 实时日志输出

**方案：** SSE 端点 tail `journalctl`

**接口：**
- `GET /api/logs` — 返回 `text/event-stream`
  - 后端用 `subprocess.Popen(["journalctl", "-u", "voice-assistant", "-f", "--output=short-precise"])` 持续读取
  - 每行作为一个 SSE event 推送
  - 前端滚动显示最新 200 行

**注意：** 每个 SSE 连接开一个 journalctl 子进程，断开时 terminate。

---

### 3. 历史问答记录

**方案：** 读取 `memory/history.jsonl`

**接口：**
- `GET /api/history?limit=50&offset=0` — 返回 JSON
  ```json
  {
    "messages": [
      {"role": "user", "content": "...", "timestamp": "..."},
      {"role": "assistant", "content": "...", "timestamp": "..."}
    ],
    "total": 200
  }
  ```

**前端展示：**
- 按时间倒序，每轮问答折叠显示
- 支持分页加载（每次 50 条）

---

## 共享上下文（重要）

Web 端和语音端需共用同一个 `MemoryStore` 实例（同一会话历史、Bootstrap 文件）。

**方案：** `web_server.py` 在启动时初始化全局 `MemoryStore`，每次 `/api/chat` 请求调用 `stream_llm()`，传入全局 history 列表。

> 注意：voice-assistant.service 和 web_server 同时运行时，两者都会 **追加写** `history.jsonl`。JSONL 追加写是安全的（append-only），但 history 列表各自独立，**不共享内存对话上下文**。后续可用文件锁或统一进程解决。

---

## 部署

```bash
# 安装依赖（一次性）
source .venv/bin/activate
pip install fastapi uvicorn

# 开发运行
uvicorn web_server:app --host 0.0.0.0 --port 8080 --reload

# 生产（新建 systemd service）
# web-assistant.service — 与 voice-assistant.service 并行运行
```

访问地址：`http://<Pi-IP>:8080`

---

## 实现顺序

1. `web_server.py` 骨架 + `/api/chat` SSE 端点（验证 LLM 流式通路）
2. `index.html` 对话界面 + Web Speech API 接入
3. `/api/logs` SSE 端点 + 前端日志面板
4. `/api/history` 端点 + 前端历史面板
5. `web-assistant.service` systemd 单元文件
