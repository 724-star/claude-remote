# 📱 Claude Code 手机远程控制 —— 完整部署教程

> 手机远程操控本机 Claude Code，实时查看执行结果。
> 支持局域网/内网优先，外网可配置端口映射或 frp 穿透。

---

## 📦 项目结构

```
claude-remote/
├── server.py            # FastAPI 后端服务（主程序）
├── static/
│   └── index.html       # 手机端 Web 界面（单页）
├── config.json          # 配置（首次运行自动生成）
├── history.json         # 任务历史（自动生成）
└── deployment_guide.md  # 本文件
```

---

## 🚀 第一步：安装依赖

```bash
# 打开终端（CMD 或 PowerShell 或 Git Bash）

# 进入项目目录
cd C:\Users\七七\claude-remote

# 安装 Python 依赖
pip install fastapi uvicorn websockets
```

> **Windows 用户注意：** 确保 Python 已添加到 PATH。如果 `pip` 报错，尝试：
> ```bash
> python -m pip install fastapi uvicorn websockets
> ```

---

## 🚀 第二步：启动服务

```bash
# 在项目目录中执行
python server.py
```

启动后你会看到类似这样的输出：

```
  ╔══════════════════════════════════════════════════╗
  ║      Claude Code 远程控制服务端                    ║
  ║      Remote Control for Claude Code              ║
  ╠══════════════════════════════════════════════════╣
  ║  地址: http://0.0.0.0:8765                       ║
  ║  Token: a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6       ║
  ║  模型: deepseek-v4-flash                         ║
  ║  超时: 600秒                                      ║
  ╚══════════════════════════════════════════════════╝
```

> ⚠️ **首次启动**会自动生成 `config.json` 和随机 Token，请**务必记下这个 Token**，手机连接时需要。

### 后台运行（锁屏不断连）

**方法一：最小化窗口（最简单）**
把终端窗口最小化即可，服务继续运行。

**方法二：Windows 服务（开机自启）**
用 [NSSM](https://nssm.cc/download) 注册为 Windows 服务：
```bash
# 下载 nssm，然后在管理员终端执行
nssm install ClaudeRemote "C:\Users\七七\claude-remote\run.bat"
```

创建 `run.bat`：
```batch
@echo off
cd /d C:\Users\七七\claude-remote
python server.py
```

**方法三：使用 pythonw 无窗口运行**
```bash
pythonw server.py
```
> `pythonw` 不显示控制台窗口，适合后台运行。日志输出到文件。

**方法四：PowerShell 隐藏窗口**
```powershell
Start-Process -WindowStyle Hidden -FilePath "python" -ArgumentList "server.py"
```

---

## 🚀 第三步：局域网连接（手机访问）

### 3.1 找到本机局域网 IP

```bash
# 在命令行执行
ipconfig

# 找到类似这样的行（通常是 192.168.x.x）：
#   IPv4 地址 . . . . . . . . . . . . : 192.168.1.100
```

### 3.2 手机浏览器打开

1. **确保手机和电脑连的是同一个 WiFi**
2. 手机浏览器打开：
   ```
   http://192.168.1.100:8765
   ```
   > 把 `192.168.1.100` 换成你查到的实际 IP
3. 输入启动时显示的 Token，点击「连接」
4. ✅ 连接成功！现在可以在手机上给 Claude 下发指令了

### 常见问题排查

| 问题 | 解决方法 |
|------|---------|
| 手机打不开页面 | 检查防火墙，看下方【防火墙配置】 |
| Token 无效 | 检查 URL 中 token 参数或输入是否正确 |
| 连接后没反应 | 检查 Claude Code 是否正确安装 (`claude --version`) |
| 输出乱码 | 服务器默认 UTF-8 编码，手机浏览器一般自动适配 |

### Windows 防火墙配置

```bash
# 管理员权限运行 PowerShell：

# 添加防火墙规则（允许 8765 端口入站）
New-NetFirewallRule -DisplayName "ClaudeRemote" -Direction Inbound `
  -Protocol TCP -LocalPort 8765 -Action Allow
```

---

## 🌐 第四步：外网远程访问（可选）

> ⚠️ **安全警告：** 将本机暴露到公网存在安全风险。
> - 请务必使用**强 Token**（默认已自动生成 32 位随机 hex）
> - 建议仅在外出必要时开启，用完关闭
> - 不要在公共网络（咖啡馆、机场 WiFi）开启端口映射

### 方案 A：路由器端口映射（推荐，如果可控路由器）

1. 在路由器管理后台找到 **端口转发/虚拟服务器** 功能
2. 添加规则：
   - 外部端口：`8765`（可改成其他端口）
   - 内部 IP：`192.168.1.100`（你的电脑 IP）
   - 内部端口：`8765`
   - 协议：`TCP`
3. 获取你的公网 IP：访问 https://ipinfo.io/ip
4. 手机访问：`http://你的公网IP:8765`

> ⚠️ 多数家庭宽带没有固定公网 IP，IP 会变。可以用 `ddns`（动态域名），如：
> - 阿里云 DDNS
> - No-IP (https://www.noip.com)
> - Oray 花生壳

### 方案 B：frp 内网穿透（无路由器控制权）

#### 你需要一台有公网 IP 的服务器（云服务器/VPS）

**服务端（云服务器上执行）：**

```bash
# 1. 下载 frp
wget https://github.com/fatedier/frp/releases/latest/download/frp_0.58.0_linux_amd64.tar.gz
tar -xzf frp_*.tar.gz
cd frp_*/

# 2. 编辑 frps.toml
cat > frps.toml << 'EOF'
[common]
bind_port = 7000
token = "你的frp密码"   # 修改为强密码
EOF

# 3. 启动
./frps -c frps.toml
```

**客户端（你的 Windows 电脑上执行）：**

```bash
# 1. 下载 frp Windows 版
#    从 https://github.com/fatedier/frp/releases 下载 windows_amd64 版

# 2. 解压后编辑 frpc.toml
```

`frpc.toml` 内容：
```toml
server_addr = "你的云服务器IP"
server_port = 7000
token = "你的frp密码"

[[proxies]]
name = "claude-remote"
type = "tcp"
local_ip = "127.0.0.1"
local_port = 8765
remote_port = 8765
```

```bash
# 3. 启动 frp 客户端
frpc -c frpc.toml
```

**手机端访问：**
```
http://你的云服务器IP:8765
```

### 方案 C：Cloudflare Tunnel（进阶，推荐）

需要有一个域名，并托管到 Cloudflare：
```bash
# 安装 cloudflare tunnel
# 参考 https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/
```

---

## 💡 使用示例

### 示例 1：编写 Python 脚本

**手机输入：**
```
用 Python 写一个文件批量重命名工具，支持正则表达式匹配
```

**Claude Code 返回：**
```python
import re
import os
from pathlib import Path

def batch_rename(directory, pattern, replacement):
    """批量重命名文件"""
    dir_path = Path(directory)
    for file in dir_path.iterdir():
        if file.is_file():
            new_name = re.sub(pattern, replacement, file.name)
            if new_name != file.name:
                file.rename(dir_path / new_name)
                print(f"重命名: {file.name} -> {new_name}")

if __name__ == "__main__":
    batch_rename(".", r"\((\d+)\)", r"[\1]")
```
> 在手机上实时看到 Claude 生成的完整代码。

### 示例 2：文件处理任务

**手机输入：**
```
找出桌面上所有超过 100MB 的文件，列出路径和大小
```

**Claude Code 返回：**
```
在 C:\Users\七七\Desktop\ 目录下找到以下大文件：
- project_backup.zip — 256.3 MB
- video_demo.mp4 — 1.2 GB
- iso_image.iso — 4.7 GB

共 3 个文件，总计 6.2 GB
```

### 示例 3：代码分析与修复

**手机输入：**
```
帮我审查以下代码的安全问题：
def login(username, password):
    query = f"SELECT * FROM users WHERE name='{username}' AND pass='{password}'"
    return db.execute(query)  # 有 SQL 注入风险
```

**Claude Code 返回：**
```
发现以下安全问题：

1. ❌ SQL 注入漏洞（高危）
   - 直接拼接用户输入到 SQL 查询字符串
   - 攻击者可构造特殊输入获取未授权数据
   - 修复方案：使用参数化查询

2. ✅ 建议修复代码：
   def login(username, password):
       query = "SELECT * FROM users WHERE name=? AND pass=?"
       return db.execute(query, (username, password))
```

---

## 🎛️ 配置说明

`config.json`（首次运行自动生成）：

```json
{
  "server": {
    "host": "0.0.0.0",       // 监听地址，0.0.0.0 表示所有网卡
    "port": 8765,             // 端口号
    "auth_token": "xxx"       // 认证 Token（自动生成）
  },
  "claude": {
    "command": "claude",       // Claude Code 命令
    "timeout_seconds": 600,    // 单次任务超时（秒）
    "strip_ansi": true,        // 去除 ANSI 颜色码
    "model": "deepseek-v4-flash"  // 当前模型
  },
  "security": {
    "lan_only": false,         // true=仅允许局域网
    "allowed_ips": [],         // 白名单 IP，空=不限制
    "rate_limit_per_minute": 60  // 每分钟最大请求数
  }
}
```

### 配置修改示例

修改端口：
```bash
# 编辑 config.json，将 port 改为 8888
# 重启服务即可
```

刷新 Token：
```bash
# 调用 API（需要旧 Token）
curl -X POST "http://localhost:8765/api/token/refresh?token=旧Token"
```

切换模型：
```bash
# 方法1：手机端点击模型徽章切换
# 方法2：直接修改 config.json 中的 model 值
# 方法3：在 CLAUDE.md 中修改 model: 一行
```

---

## 🔧 推荐 / 命令分类

| 命令 | 说明 | 处理方式 |
|------|------|---------|
| `/model <name>` | 切换 Claude 模型 | 服务端处理（更新 CLAUDE.md） |
| `/help` | 显示帮助信息 | 服务端处理 |
| `/history` | 查看最近任务 | 服务端处理 |
| `/clear` | 清空当前输出 | 前端处理 |
| 普通文本 | 任务提问 | 透传给 Claude Code |
| `/其他` | 其他 Claude 命令 | 透传给 Claude Code |

---

## 🔄 异常处理机制

### 断线重连
- 手机端 WebSocket 断开后自动在 **10 秒后重连**
- 网络短暂波动不会丢失正在执行的任务
- 任务执行过程中断线 → 服务端继续执行 → 重连后可查看历史记录

### 超时处理
- 单次任务默认 **600 秒（10 分钟）** 超时
- 超时后自动终止 Claude 进程并返回错误信息
- 可在 `config.json` 中调整 `claude.timeout_seconds`

### 错误捕获
| 错误场景 | 处理方式 |
|---------|---------|
| Claude 命令不存在 | 返回安装指引 |
| Claude 进程崩溃 | 返回报错日志 |
| API 调用失败 | 透传 Claude 的错误输出 |
| Token 无效 | 拒绝连接，提示重新输入 |
| 端口被占用 | 启动时提示修改端口 |

---

## ⚙️ 技术架构

```
┌─────────────────────────────────────────────────────────┐
│  手机浏览器 (Mobile Browser)                             │
│  - 单页 Web App (index.html)                            │
│  - WebSocket 实时通信                                    │
│  - 任务历史 + 重发                                       │
└──────────────────────┬──────────────────────────────────┘
                       │  HTTP + WebSocket
                       ▼
┌─────────────────────────────────────────────────────────┐
│  Python 服务端 (FastAPI)                                 │
│  - WebSocket 端点 /ws                                   │
│  - REST API: /api/history, /api/claude/model            │
│  - Token 鉴权                                            │
│  - CORS 支持                                              │
│  - 静态文件服务                                           │
└──────────────────────┬──────────────────────────────────┘
                       │  subprocess
                       ▼
┌─────────────────────────────────────────────────────────┐
│  Claude Code CLI (子进程)                                │
│  - 每次任务新建进程                                       │
│  - 流式输出实时转发                                       │
│  - ANSI 码剥离                                            │
│  - 超时 / 取消控制                                        │
└─────────────────────────────────────────────────────────┘
```

---

## ❓ 常见问题 FAQ

**Q: 启动后提示 `claude: command not found`？**
> 确保 Claude Code 已安装。运行 `npx claude --version` 或 `claude --version` 测试。
> 然后修改 `config.json` 中的 `claude.command` 为实际路径。

**Q: 手机和电脑不在同一个 WiFi 怎么办？**
> 使用外网远程访问方案（见第四步），推荐 frp 或路由器端口映射。

**Q: 任务执行一半不小心关闭手机页面？**
> 服务端会继续执行直到完成。重新连接后，在历史记录中可以查看结果。

**Q: 如何永久关闭服务？**
> 在终端按 `Ctrl+C` 即可。

**Q: 可以同时连接多个手机吗？**
> 当前版本设计为一对一控制。如果有并发控制需求，可以修改 `server.py` 实现。
> 但多机同时发送命令可能导致冲突，不推荐。

**Q: 服务运行中电脑休眠了怎么办？**
> 请在 Windows 电源设置中关闭「睡眠」：
> ```
> 设置 → 系统 → 电源 → 睡眠设置为"从不"
> ```

**Q: 每次启动 Token 都会变吗？**
> 不会。Token 首次启动生成后保存在 `config.json`，后续启动保持不变。
> 如需更换 Token，调用 `/api/token/refresh` 接口。

---

## 📝 更新日志

### v1.0 (2026-06-24)
- 初版发布
- 完整的 WebSocket 实时通信
- 手机端单页 Web UI
- Token 鉴权
- 任务历史管理
- 模型切换支持
- 局域网/外网双模式
