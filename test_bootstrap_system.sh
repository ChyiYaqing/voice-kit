#!/bin/bash
# Test script for Bootstrap system (OpenClaw-inspired)
# Tests SOUL.md, IDENTITY.md, USER.md creation and loading

set -e

# Use venv if available
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

echo "=== Voice-Kit Bootstrap System Tests ==="
echo ""

# Test directory
TEST_DIR="/tmp/test-voice-kit-bootstrap"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

function pass() {
    echo -e "${GREEN}✓${NC} $1"
}

function warn() {
    echo -e "${YELLOW}⚠${NC} $1"
}

function fail() {
    echo -e "${RED}✗${NC} $1"
    exit 1
}

# Test 1: Create all Bootstrap files
echo "[Test 1] Create default Bootstrap files"
rm -rf "$TEST_DIR"
python3 -c "
import sys
sys.path.insert(0, '$(pwd)')
from memory_store import MemoryStore
from pathlib import Path

store = MemoryStore(Path('$TEST_DIR'))

# Create all Bootstrap files
store.create_default_soul()
store.create_default_identity()
store.create_default_user()
store.create_default_memory()

# Verify all exist
assert store.soul_path.exists(), 'SOUL.md should exist'
assert store.identity_path.exists(), 'IDENTITY.md should exist'
assert store.user_path.exists(), 'USER.md should exist'
assert store.memory_path.exists(), 'MEMORY.md should exist'
" 2>&1 && pass "All Bootstrap files created" || fail "Failed to create Bootstrap files"

# Test 2: Load Bootstrap files
echo ""
echo "[Test 2] Load Bootstrap files"
python3 -c "
import sys
sys.path.insert(0, '$(pwd)')
from memory_store import MemoryStore
from pathlib import Path

store = MemoryStore(Path('$TEST_DIR'))

# Load all files
soul = store.load_soul()
identity = store.load_identity()
user = store.load_user()
memory = store.load_memory()

assert soul, 'SOUL.md should not be empty'
assert identity, 'IDENTITY.md should not be empty'
assert user, 'USER.md should not be empty'
assert memory, 'MEMORY.md should not be empty'

assert '语音助手的灵魂' in soul, 'SOUL.md should contain Chinese template'
assert '助手身份' in identity, 'IDENTITY.md should contain identity template'
assert '用户画像' in user, 'USER.md should contain user template'
assert 'Assistant Memory' in memory, 'MEMORY.md should contain memory template'
" 2>&1 && pass "All Bootstrap files loaded correctly" || fail "Failed to load Bootstrap files"

# Test 3: Update Bootstrap files
echo ""
echo "[Test 3] Update Bootstrap files"
python3 -c "
import sys
sys.path.insert(0, '$(pwd)')
from memory_store import MemoryStore
from pathlib import Path

store = MemoryStore(Path('$TEST_DIR'))

# Update SOUL.md
soul = store.load_soul()
soul += '\n\n## 灵魂演化\n- [2026-03-18 10:30] 调整对话风格为更幽默'
store.update_soul(soul)

# Update IDENTITY.md
identity = store.load_identity()
identity += '\n\n## 演化记录\n- [2026-03-18 10:30] 用户给我起名叫"小智"'
store.update_identity(identity)

# Update USER.md
user = store.load_user()
user += '\n\n## 基本信息\n- 用户名: 老王\n- 称呼偏好: 老王'
store.update_user(user)

# Verify updates
soul_updated = store.load_soul()
identity_updated = store.load_identity()
user_updated = store.load_user()

assert '灵魂演化' in soul_updated, 'SOUL.md should be updated'
assert '演化记录' in identity_updated, 'IDENTITY.md should be updated'
assert '老王' in user_updated, 'USER.md should be updated'
" 2>&1 && pass "All Bootstrap files updated successfully" || fail "Failed to update Bootstrap files"

# Test 4: Bootstrap injection (build_system_prompt)
echo ""
echo "[Test 4] Bootstrap injection (system prompt building)"
python3 -c "
import sys
sys.path.insert(0, '$(pwd)')
from memory_store import MemoryStore
from pathlib import Path

# Simple build_system_prompt simulation
def build_system_prompt(soul='', identity='', user='', memory=''):
    parts = []
    if soul:
        parts.append('# 你的灵魂')
        parts.append(soul)
    if identity:
        parts.append('# 你的身份')
        parts.append(identity)
    if user:
        parts.append('# 你在帮助的人')
        parts.append(user)
    if memory:
        parts.append('# 长期记忆')
        parts.append(memory)
    return '\n\n'.join(parts)

store = MemoryStore(Path('$TEST_DIR'))

# Load all Bootstrap files
soul = store.load_soul()
identity = store.load_identity()
user = store.load_user()
memory = store.load_memory()

# Build system prompt
prompt = build_system_prompt(soul, identity, user, memory)

# Verify all sections present
assert '你的灵魂' in prompt, 'Should contain SOUL section'
assert '你的身份' in prompt, 'Should contain IDENTITY section'
assert '你在帮助的人' in prompt, 'Should contain USER section'
assert '长期记忆' in prompt, 'Should contain MEMORY section'

# Check ordering (SOUL before IDENTITY before USER before MEMORY)
soul_pos = prompt.index('你的灵魂')
identity_pos = prompt.index('你的身份')
user_pos = prompt.index('你在帮助的人')
memory_pos = prompt.index('长期记忆')

assert soul_pos < identity_pos < user_pos < memory_pos, 'Sections should be in correct order'

print(f'System prompt built successfully ({len(prompt)} chars)')
" 2>&1 && pass "System prompt injection working" || fail "System prompt injection failed"

# Test 5: Size limits
echo ""
echo "[Test 5] Bootstrap size validation"
python3 -c "
import sys
sys.path.insert(0, '$(pwd)')
from memory_store import MemoryStore
from pathlib import Path

# Default limits (from config.py)
BOOTSTRAP_MAX_CHARS = 20000
BOOTSTRAP_TOTAL_MAX_CHARS = 150000

store = MemoryStore(Path('$TEST_DIR'))

soul = store.load_soul()
identity = store.load_identity()
user = store.load_user()
memory = store.load_memory()

total_size = len(soul) + len(identity) + len(user) + len(memory)

print(f'Bootstrap sizes:')
print(f'  SOUL: {len(soul)} chars')
print(f'  IDENTITY: {len(identity)} chars')
print(f'  USER: {len(user)} chars')
print(f'  MEMORY: {len(memory)} chars')
print(f'  TOTAL: {total_size} chars')
print(f'  Max per file: {BOOTSTRAP_MAX_CHARS} chars')
print(f'  Total max: {BOOTSTRAP_TOTAL_MAX_CHARS} chars')

assert len(soul) < BOOTSTRAP_MAX_CHARS, f'SOUL.md ({len(soul)}) exceeds per-file limit'
assert len(identity) < BOOTSTRAP_MAX_CHARS, f'IDENTITY.md ({len(identity)}) exceeds per-file limit'
assert len(user) < BOOTSTRAP_MAX_CHARS, f'USER.md ({len(user)}) exceeds per-file limit'
assert len(memory) < BOOTSTRAP_MAX_CHARS, f'MEMORY.md ({len(memory)}) exceeds per-file limit'

if total_size < BOOTSTRAP_TOTAL_MAX_CHARS:
    print(f'✓ Total size within limit')
else:
    print(f'⚠ Total size ({total_size}) exceeds limit ({BOOTSTRAP_TOTAL_MAX_CHARS})')
" 2>&1 && pass "Size validation passed" || fail "Size validation failed"

# Test 6: Idempotent file creation
echo ""
echo "[Test 6] Idempotent file creation (won't overwrite)"
python3 -c "
import sys
sys.path.insert(0, '$(pwd)')
from memory_store import MemoryStore
from pathlib import Path

store = MemoryStore(Path('$TEST_DIR'))

# Read current SOUL.md
original_soul = store.load_soul()

# Try to create again (should not overwrite)
store.create_default_soul()

# Verify unchanged
new_soul = store.load_soul()
assert original_soul == new_soul, 'SOUL.md should not be overwritten'
" 2>&1 && pass "File creation is idempotent" || fail "Idempotency check failed"

# Summary
echo ""
echo "============================================"
echo -e "${GREEN}All Bootstrap system tests passed!${NC}"
echo "============================================"
echo ""
echo "Test artifacts saved to: $TEST_DIR"
echo "You can inspect the Bootstrap files:"
echo "  cat $TEST_DIR/SOUL.md"
echo "  cat $TEST_DIR/IDENTITY.md"
echo "  cat $TEST_DIR/USER.md"
echo "  cat $TEST_DIR/MEMORY.md"
echo ""
echo "To clean up:"
echo "  rm -rf $TEST_DIR"
