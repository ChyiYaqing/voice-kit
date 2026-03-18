#!/bin/bash
# Integration test script for voice-kit memory system
# Tests memory persistence, history loading, rotation, and error recovery

set -e

# Get project root directory (parent of test/)
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Voice-Kit Memory Integration Tests ==="
echo ""

# Test directory
TEST_DIR="/tmp/test-voice-kit-memory"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

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

# Test 1: Cold start (no existing memory)
echo "[Test 1] Cold start - creating default MEMORY.md"
rm -rf "$TEST_DIR"
python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from memory_store import MemoryStore
from pathlib import Path

store = MemoryStore(Path('$TEST_DIR'))
store.create_default_memory()
memory = store.load_memory()
assert memory, 'MEMORY.md should not be empty'
assert '# Assistant Memory' in memory, 'Should contain default template'
" 2>&1 && pass "Default MEMORY.md created successfully" || fail "Failed to create default MEMORY.md"

# Test 2: Save and load messages
echo ""
echo "[Test 2] History persistence - save and load messages"
python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from memory_store import MemoryStore
from pathlib import Path

store = MemoryStore(Path('$TEST_DIR'))

# Save messages
store.save_message({'role': 'user', 'content': '你好'})
store.save_message({'role': 'assistant', 'content': '你好，有什么可以帮你的？'})
store.save_message({'role': 'user', 'content': 'GPIO怎么用？'})

# Load and verify
history = store.load_history()
assert len(history) == 3, f'Expected 3 messages, got {len(history)}'
assert history[0]['role'] == 'user', 'First message should be from user'
assert history[0]['content'] == '你好', 'First message content mismatch'
assert history[2]['content'] == 'GPIO怎么用？', 'Third message content mismatch'
" 2>&1 && pass "Messages saved and loaded correctly (3 messages)" || fail "Message save/load failed"

# Test 3: Restart persistence
echo ""
echo "[Test 3] Restart persistence - new MemoryStore instance"
python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from memory_store import MemoryStore
from pathlib import Path

# New instance (simulates restart)
store = MemoryStore(Path('$TEST_DIR'))
history = store.load_history()
assert len(history) == 3, f'Expected 3 messages after restart, got {len(history)}'
assert history[0]['content'] == '你好', 'History not persisted correctly'
" 2>&1 && pass "History persisted across restart" || fail "Restart persistence failed"

# Test 4: History rotation
echo ""
echo "[Test 4] History rotation - keep only recent messages"
python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from memory_store import MemoryStore
from pathlib import Path

store = MemoryStore(Path('$TEST_DIR'))

# Add many messages
for i in range(50):
    store.save_message({'role': 'user', 'content': f'Message {i}'})

# Rotate, keep last 10
store.rotate_history(keep_lines=10)

# Verify
history = store.load_history()
assert len(history) == 10, f'Expected 10 messages after rotation, got {len(history)}'
assert 'Message 49' in history[-1]['content'], 'Should keep most recent messages'

# Check backup exists
from pathlib import Path
backup_path = Path('$TEST_DIR') / 'history.jsonl.backup'
assert backup_path.exists(), 'Backup file should exist after rotation'
" 2>&1 && pass "Rotation successful (kept 10 most recent of 50)" || fail "History rotation failed"

# Test 5: Memory update
echo ""
echo "[Test 5] MEMORY.md updates"
python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from memory_store import MemoryStore
from pathlib import Path

store = MemoryStore(Path('$TEST_DIR'))

# Load current memory
content = store.load_memory()
assert content, 'MEMORY.md should exist'

# Update with new learning
if '## Recent Learnings' not in content:
    content += '\n\n## Recent Learnings\n'
content += '\n- [2026-03-18 10:30] User prefers concise answers'
content += '\n\n---\nLast updated: 2026-03-18 10:30:00'

store.update_memory(content)

# Verify update
updated = store.load_memory()
assert 'User prefers concise answers' in updated, 'Memory update not saved'
assert 'Last updated: 2026-03-18 10:30:00' in updated, 'Timestamp not saved'
" 2>&1 && pass "MEMORY.md updated successfully" || fail "Memory update failed"

# Test 6: Corrupted history recovery
echo ""
echo "[Test 6] Error recovery - corrupted history.jsonl"
python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from memory_store import MemoryStore
from pathlib import Path

store = MemoryStore(Path('$TEST_DIR'))

# Save valid message
store.save_message({'role': 'user', 'content': 'Valid message'})

# Manually corrupt file
history_path = Path('$TEST_DIR') / 'history.jsonl'
with open(history_path, 'a') as f:
    f.write('CORRUPTED LINE - NOT JSON\n')
    f.write('{{{{ INVALID JSON\n')

# Load - should skip corrupted lines
history = store.load_history()
assert len(history) >= 1, 'Should load at least one valid message'
assert any('Valid message' in msg.get('content', '') for msg in history), 'Valid message should be loaded'
" 2>&1 && pass "Corrupted lines skipped, valid data loaded" || fail "Error recovery failed"

# Test 7: Load with max_messages limit
echo ""
echo "[Test 7] Max messages limit"
python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from memory_store import MemoryStore
from pathlib import Path

# Clean start
import shutil
test_dir = Path('$TEST_DIR/limit_test')
if test_dir.exists():
    shutil.rmtree(test_dir)

store = MemoryStore(test_dir)

# Add 30 messages
for i in range(30):
    store.save_message({'role': 'user', 'content': f'Test {i}'})

# Load with limit
history = store.load_history(max_messages=10)
assert len(history) == 10, f'Expected 10 messages, got {len(history)}'
assert 'Test 29' in history[-1]['content'], 'Should load most recent 10'
assert 'Test 20' in history[0]['content'], 'Should start from message 20'
" 2>&1 && pass "Max messages limit working (10 of 30 loaded)" || fail "Max messages limit failed"

# Test 8: Empty directory initialization
echo ""
echo "[Test 8] Empty directory initialization"
python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from memory_store import MemoryStore
from pathlib import Path

# Completely new directory
import shutil
test_dir = Path('$TEST_DIR/fresh_start')
if test_dir.exists():
    shutil.rmtree(test_dir)

store = MemoryStore(test_dir)

# Should initialize without errors
assert test_dir.exists(), 'Directory should be created'
history = store.load_history()
assert history == [], 'History should be empty for new directory'

memory = store.load_memory()
assert memory == '', 'Memory should be empty for new directory'

# Create default
store.create_default_memory()
memory = store.load_memory()
assert memory != '', 'Default memory should be created'
assert '# Assistant Memory' in memory, 'Should contain template'
" 2>&1 && pass "Empty directory initialized successfully" || fail "Empty directory initialization failed"

# Summary
echo ""
echo "============================================"
echo -e "${GREEN}All integration tests passed!${NC}"
echo "============================================"
echo ""
echo "Test artifacts saved to: $TEST_DIR"
echo "You can inspect the files manually:"
echo "  ls -lh $TEST_DIR"
echo "  cat $TEST_DIR/MEMORY.md"
echo "  head -n 5 $TEST_DIR/*/history.jsonl"
echo ""
echo "To clean up test artifacts:"
echo "  rm -rf $TEST_DIR"
