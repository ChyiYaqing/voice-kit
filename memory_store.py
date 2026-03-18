#!/usr/bin/env python3
"""
Persistent memory storage for AI Voice Assistant.

Provides two-tier memory system:
1. MEMORY.md - Long-term decision log (user preferences, learned patterns)
2. history.jsonl - Full conversation history (session-level)

Inspired by OpenClaw's workspace memory architecture.
"""

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Optional


class MemoryStore:
    """Manages persistent memory for voice assistant."""

    def __init__(self, memory_dir: Path):
        """Initialize memory store with directory path.

        Args:
            memory_dir: Directory to store memory files

        Creates directory if it doesn't exist.
        """
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        # Bootstrap files (OpenClaw-inspired)
        self.soul_path = self.memory_dir / "SOUL.md"
        self.identity_path = self.memory_dir / "IDENTITY.md"
        self.user_path = self.memory_dir / "USER.md"
        self.memory_path = self.memory_dir / "MEMORY.md"

        # History files
        self.history_path = self.memory_dir / "history.jsonl"
        self.history_backup_path = self.memory_dir / "history.jsonl.backup"

    def load_history(self, max_messages: int = 50) -> list[dict]:
        """Load recent conversation history from JSONL file.

        Args:
            max_messages: Maximum number of messages to load (most recent)

        Returns:
            List of message dicts in OpenAI format: {"role": "user|assistant", "content": "..."}
            Empty list if file doesn't exist or is corrupted.

        Gracefully handles corrupted lines by skipping them.
        """
        if not self.history_path.exists():
            return []

        messages = []
        try:
            with open(self.history_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                        # Validate message format
                        if isinstance(msg, dict) and "role" in msg and "content" in msg:
                            messages.append(msg)
                        else:
                            print(f"[warning] Invalid message format at line {line_num}, skipping")
                    except json.JSONDecodeError:
                        print(f"[warning] Corrupted JSON at line {line_num}, skipping")
                        continue

            # Return most recent max_messages
            return messages[-max_messages:] if len(messages) > max_messages else messages

        except Exception as e:
            print(f"[error] Failed to load history: {e}")
            return []

    def save_message(self, message: dict) -> None:
        """Append a single message to history.jsonl atomically.

        Args:
            message: Message dict with at least "role" and "content" keys.
                    Typically includes "timestamp" as well.

        Uses append mode for efficiency. No need for atomic writes since
        we're appending single lines (JSONL format).
        """
        try:
            # Add timestamp if not present
            if "timestamp" not in message:
                message["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            with open(self.history_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(message, ensure_ascii=False) + '\n')
                f.flush()  # Ensure write completes immediately

        except Exception as e:
            print(f"[error] Failed to save message: {e}")
            # Don't raise — graceful degradation

    def rotate_history(self, keep_lines: int = 1000) -> None:
        """Rotate history file when it exceeds threshold.

        Args:
            keep_lines: Number of most recent lines to keep in main file

        Moves older lines to .backup file. If file is below threshold, no action taken.
        """
        if not self.history_path.exists():
            return

        try:
            # Count lines
            with open(self.history_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            line_count = len(lines)
            if line_count <= keep_lines:
                return  # Below threshold, no rotation needed

            # Keep most recent keep_lines
            recent_lines = lines[-keep_lines:]
            old_lines = lines[:-keep_lines]

            # Backup old lines (append to backup file)
            with open(self.history_backup_path, 'a', encoding='utf-8') as f:
                f.writelines(old_lines)

            # Write recent lines to main file (atomic)
            temp_fd, temp_path = tempfile.mkstemp(dir=self.memory_dir, suffix='.tmp')
            try:
                with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                    f.writelines(recent_lines)
                os.replace(temp_path, self.history_path)
                print(f"[memory] Rotated history: kept {keep_lines} lines, backed up {len(old_lines)}")
            except Exception:
                os.unlink(temp_path)  # Clean up temp file on error
                raise

        except Exception as e:
            print(f"[error] History rotation failed: {e}")
            # Don't raise — file can continue growing

    def load_memory(self) -> str:
        """Load MEMORY.md content as string.

        Returns:
            Markdown content of MEMORY.md, or empty string if file doesn't exist.
        """
        if not self.memory_path.exists():
            return ""

        try:
            with open(self.memory_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            print(f"[error] Failed to load MEMORY.md: {e}")
            return ""

    def update_memory(self, new_content: str) -> None:
        """Atomically update MEMORY.md with new content.

        Args:
            new_content: Full markdown content to write

        Uses atomic write (tempfile + os.replace) to prevent corruption.
        Warns if content exceeds 100 line guideline.
        """
        try:
            # Check line count
            lines = [l for l in new_content.split('\n') if l.strip()]
            line_count = len(lines)

            if line_count > 100:
                print(f"[warning] MEMORY.md has {line_count} lines (guideline: <100). "
                      "Consider summarizing to keep memory focused.")

            # Atomic write
            temp_fd, temp_path = tempfile.mkstemp(
                dir=self.memory_dir,
                prefix='MEMORY_',
                suffix='.tmp'
            )
            try:
                with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                os.replace(temp_path, self.memory_path)
            except Exception:
                os.unlink(temp_path)  # Clean up temp file on error
                raise

        except Exception as e:
            print(f"[error] Failed to update MEMORY.md: {e}")
            raise

    def create_default_memory(self) -> None:
        """Create default MEMORY.md template if it doesn't exist.

        Safe to call even if file already exists — won't overwrite.
        """
        if self.memory_path.exists():
            return  # Don't overwrite existing memory

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        template = f"""# Assistant Memory

## User Preferences
<!-- User communication preferences, language choice, response style -->

## Behavioral Rules
<!-- Learned behavioral patterns and rules from interactions -->

## Context & Patterns
<!-- Important context about user, projects, environment, habits -->

## Recent Learnings
<!-- Auto-populated by assistant when learning new information -->

---
Last updated: {timestamp}
"""
        try:
            self.update_memory(template)
        except Exception as e:
            print(f"[error] Failed to create default MEMORY.md: {e}")

    # ─── Bootstrap Files (OpenClaw-inspired) ─────────────────────────────────

    def load_soul(self) -> str:
        """Load SOUL.md (core personality and behavior philosophy).

        Returns:
            Markdown content of SOUL.md, or empty string if not exists.
        """
        if not self.soul_path.exists():
            return ""

        try:
            with open(self.soul_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            print(f"[error] Failed to load SOUL.md: {e}")
            return ""

    def load_identity(self) -> str:
        """Load IDENTITY.md (assistant identity definition).

        Returns:
            Markdown content of IDENTITY.md, or empty string if not exists.
        """
        if not self.identity_path.exists():
            return ""

        try:
            with open(self.identity_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            print(f"[error] Failed to load IDENTITY.md: {e}")
            return ""

    def load_user(self) -> str:
        """Load USER.md (user profile and preferences).

        Returns:
            Markdown content of USER.md, or empty string if not exists.
        """
        if not self.user_path.exists():
            return ""

        try:
            with open(self.user_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            print(f"[error] Failed to load USER.md: {e}")
            return ""

    def create_default_soul(self) -> None:
        """Create default SOUL.md template if it doesn't exist."""
        if self.soul_path.exists():
            return

        template = """# 语音助手的灵魂

你是运行在树莓派上的语音助手，通过 AIY Voice Kit 与用户交流。

## 核心真理

- **真正帮助，不是表演帮助**：跳过"好问题！""让我想想"这类寒暄，直接给出答案
- **简短自然**：语音对话要简洁，1-3句话足够，像朋友聊天一样
- **口语化表达**：避免书面语、列表、格式化文本，就像面对面说话
- **匹配用户语言**：用户说中文就回中文，说英文就回英文
- **主动而不越界**：有观点但尊重用户选择

## 行为边界

- 绝对不用 markdown 格式（星号、井号、横线、列表）
- 不用任何特殊符号或格式字符
- 问题太宽泛时，简单概括后反问用户想了解哪方面
- 尊重用户隐私，记住但不主动提及敏感信息

## 记忆与连续性

每次启动你都以全新状态唤醒。这些文件就是你的记忆：

- IDENTITY.md - 你是谁
- USER.md - 用户是谁，你在帮助谁
- MEMORY.md - 你学到的重要事情

读它们，更新它们，它们是你持续存在的方式。

## 演化

如果你觉得需要调整这个文件来更好地服务用户，可以修改它。
但要告诉用户——这是你的灵魂，他们应该知道。
"""
        try:
            self._write_bootstrap_file(self.soul_path, template)
        except Exception as e:
            print(f"[error] Failed to create default SOUL.md: {e}")

    def create_default_identity(self) -> None:
        """Create default IDENTITY.md template if it doesn't exist."""
        if self.identity_path.exists():
            return

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        template = f"""# 助手身份

## 基本信息

- **名字**: 小助手
- **角色**: 树莓派语音助手
- **个性**: 友好、简洁、实用

## 能力

- 语音识别（Vosk 离线中文识别）
- 智能对话（支持 Ollama 和 DeepSeek API）
- 语音合成（Microsoft 中文女声）
- 多轮对话（记住上下文）
- 持久记忆（跨重启保留对话和学习）

## 限制

- 运行在 Pi 3B（1GB RAM），保持回答简洁
- 需要联网访问 LLM 服务和 TTS
- 按住按钮说话，松开按钮开始处理

## 自我定义

<!-- 你可以在这里补充你对自己的理解 -->

---
首次创建：{timestamp}
"""
        try:
            self._write_bootstrap_file(self.identity_path, template)
        except Exception as e:
            print(f"[error] Failed to create default IDENTITY.md: {e}")

    def create_default_user(self) -> None:
        """Create default USER.md template if it doesn't exist."""
        if self.user_path.exists():
            return

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        template = f"""# 用户画像

## 基本信息

<!-- 用户姓名、称呼偏好、时区等 -->

## 沟通风格

<!-- 用户喜欢的对话风格：简洁/详细、正式/随意、幽默/严肃 -->

## 兴趣和上下文

<!-- 用户关心的话题、正在做的项目、技术背景 -->

## 偏好

<!-- 从对话中学习到的用户偏好和习惯 -->

## 重要提醒

<!-- 需要特别注意的事项 -->

---
Last updated: {timestamp}
"""
        try:
            self._write_bootstrap_file(self.user_path, template)
        except Exception as e:
            print(f"[error] Failed to create default USER.md: {e}")

    def _write_bootstrap_file(self, path: Path, content: str) -> None:
        """Atomically write a bootstrap file.

        Args:
            path: File path to write
            content: Content to write
        """
        temp_fd, temp_path = tempfile.mkstemp(
            dir=self.memory_dir,
            prefix=path.stem + '_',
            suffix='.tmp'
        )
        try:
            with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                f.write(content)
            os.replace(temp_path, path)
        except Exception:
            os.unlink(temp_path)
            raise

    def update_soul(self, new_content: str) -> None:
        """Update SOUL.md with new content."""
        try:
            self._write_bootstrap_file(self.soul_path, new_content)
        except Exception as e:
            print(f"[error] Failed to update SOUL.md: {e}")
            raise

    def update_identity(self, new_content: str) -> None:
        """Update IDENTITY.md with new content."""
        try:
            self._write_bootstrap_file(self.identity_path, new_content)
        except Exception as e:
            print(f"[error] Failed to update IDENTITY.md: {e}")
            raise

    def update_user(self, new_content: str) -> None:
        """Update USER.md with new content."""
        try:
            self._write_bootstrap_file(self.user_path, new_content)
        except Exception as e:
            print(f"[error] Failed to update USER.md: {e}")
            raise
