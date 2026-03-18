#!/usr/bin/env python3
"""Quick manual test for memory system - simulates assistant usage."""

import sys
from pathlib import Path

# Add project root to path to import memory_store
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from memory_store import MemoryStore
import time

def test_memory_system():
    """Interactive memory system test."""
    print("=== Memory System Quick Test ===\n")

    # Initialize memory store (use project root's memory dir)
    memory_dir = PROJECT_ROOT / "memory"
    store = MemoryStore(memory_dir)

    # Test 1: Create default memory
    print("[1] Creating default MEMORY.md...")
    store.create_default_memory()
    memory_content = store.load_memory()
    print(f"✓ MEMORY.md created ({len(memory_content)} chars)\n")

    # Test 2: Save conversation history
    print("[2] Saving conversation history...")
    conversations = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！有什么可以帮你的？"},
        {"role": "user", "content": "我喜欢简短的回答"},
        {"role": "assistant", "content": "好的，我记住了。"},
    ]

    for msg in conversations:
        store.save_message(msg)
        print(f"  Saved: {msg['role']}: {msg['content'][:30]}...")

    # Test 3: Load and verify
    print("\n[3] Loading history...")
    history = store.load_history()
    print(f"✓ Loaded {len(history)} messages")
    for i, msg in enumerate(history, 1):
        print(f"  {i}. {msg['role']}: {msg['content']}")

    # Test 4: Update MEMORY.md with learnings
    print("\n[4] Updating MEMORY.md with learnings...")
    memory_content = store.load_memory()

    # Add to Recent Learnings section
    if "## Recent Learnings" in memory_content:
        # Find the section and add after it
        lines = memory_content.split('\n')
        insert_idx = None
        for i, line in enumerate(lines):
            if line.startswith('## Recent Learnings'):
                # Find next empty line after section header
                for j in range(i+1, len(lines)):
                    if lines[j].strip() == '' or lines[j].startswith('<!--'):
                        insert_idx = j + 1
                        break
                break

        if insert_idx:
            timestamp = time.strftime("%Y-%m-%d %H:%M")
            new_learning = f"- [{timestamp}] User prefers concise answers"
            lines.insert(insert_idx, new_learning)
            memory_content = '\n'.join(lines)

    store.update_memory(memory_content)
    print("✓ MEMORY.md updated with learning\n")

    # Test 5: Verify update
    print("[5] Verifying MEMORY.md update...")
    updated_memory = store.load_memory()
    if "User prefers concise answers" in updated_memory:
        print("✓ Learning successfully saved to MEMORY.md\n")
    else:
        print("✗ Failed to save learning\n")

    # Summary
    print("=" * 50)
    print("Test completed! Check files:")
    print(f"  MEMORY.md:      {memory_dir / 'MEMORY.md'}")
    print(f"  history.jsonl:  {memory_dir / 'history.jsonl'}")
    print("\nTo inspect:")
    print(f"  cat {memory_dir / 'MEMORY.md'}")
    print(f"  cat {memory_dir / 'history.jsonl'}")
    print("=" * 50)

if __name__ == "__main__":
    test_memory_system()
