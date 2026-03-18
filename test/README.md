# Memory System Tests

测试脚本用于验证voice-kit的持久化记忆功能。

## 测试文件

### 1. `test_memory_integration.sh`
**完整集成测试套件** - 自动化测试所有核心功能

**测试覆盖：**
- ✓ 冷启动（创建默认MEMORY.md）
- ✓ 消息持久化（保存和加载）
- ✓ 重启后数据恢复
- ✓ 历史记录轮转（超过阈值时备份旧数据）
- ✓ MEMORY.md更新
- ✓ 错误恢复（处理损坏的JSON）
- ✓ 消息数量限制
- ✓ 空目录初始化

**运行方式：**
```bash
# 从项目根目录运行
bash test/test_memory_integration.sh

# 或直接运行
./test/test_memory_integration.sh
```

**输出示例：**
```
=== Voice-Kit Memory Integration Tests ===

[Test 1] Cold start - creating default MEMORY.md
✓ Default MEMORY.md created successfully

[Test 2] History persistence - save and load messages
✓ Messages saved and loaded correctly (3 messages)

...

============================================
All integration tests passed!
============================================
```

### 2. `quick_test_memory.py`
**快速功能验证** - 模拟实际使用场景

**功能：**
- 创建默认记忆文件
- 保存对话历史
- 更新长期记忆（MEMORY.md）
- 验证数据持久化

**运行方式：**
```bash
# 从项目根目录运行
python3 test/quick_test_memory.py

# 或使用虚拟环境
source .venv/bin/activate
python test/quick_test_memory.py
```

**注意：** 此脚本会在项目的 `memory/` 目录创建真实文件，不影响测试环境。

## 测试数据

**临时文件位置：**
- 集成测试：`/tmp/test-voice-kit-memory/`（自动清理）
- 快速测试：`./memory/`（项目目录）

**清理测试数据：**
```bash
# 清理集成测试数据
rm -rf /tmp/test-voice-kit-memory

# 清理项目memory目录（谨慎使用！）
rm -rf ./memory
```

## 在树莓派上测试

部署到树莓派后，可以进行实际运行测试：

```bash
# SSH到树莓派
ssh chyiyaqing@raspberrypi.local

# 进入项目目录
cd ~/voice-kit

# 运行测试
bash test/test_memory_integration.sh

# 启动assistant，进行实际对话测试
sudo systemctl start voice-assistant

# 观察日志，确认memory加载
journalctl -u voice-assistant -f | grep -i memory

# 验证文件生成
ls -lh memory/
cat memory/MEMORY.md
tail -10 memory/history.jsonl
```

## CI/CD集成

可以在部署流程中添加测试步骤：

```bash
# setup.sh 中可以添加（可选）
if [ -f test/test_memory_integration.sh ]; then
    echo "Running memory system tests..."
    bash test/test_memory_integration.sh || {
        echo "Tests failed, but continuing installation..."
    }
fi
```

## 故障排查

**导入错误：**
```
ModuleNotFoundError: No module named 'memory_store'
```
→ 确保从项目根目录运行，或检查sys.path设置

**权限错误：**
```
PermissionError: [Errno 13] Permission denied: './memory'
```
→ 确保运行用户有写入权限：`chmod 755 ./memory`

**JSONL损坏：**
```
[warning] Corrupted JSON at line X, skipping
```
→ 正常行为，系统会自动跳过损坏行，继续加载有效数据
