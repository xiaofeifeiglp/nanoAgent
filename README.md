# nanoAgent

一套面向教学的 **极简 Agent 演进样例库**：从"最简可运行 Agent 循环"出发，每一层目录叠加一项关键能力，沿着 essence → memory → skills/mcp → subagent → teams → compact → safety 的路线，逐步演化出一个接近生产形态的 Agent 运行时。

每个目录都是一个独立的可运行 Python 文件 (`agent-*.py`)，依赖相同（仅 `openai` + `httpx`），共用同一份 `OpenAI` 客户端配置；上层目录的实现会**复用**下层目录的形态，只新增特性、不重写循环。

---

## 特性总览

| 模块 | 新增能力 | 关键概念 |
| --- | --- | --- |
| `01-essence` | 工具调用循环 | `messages` 列表 + `tool_calls` 分发 |
| `02-memory` | 跨会话持久记忆 | `MEMORY.md` 追加 + 注入 system prompt |
| `03-skills-mcp` | 外部知识与外部工具 | `.agent/rules` + `.agent/skills` + `.agent/mcp.json` |
| `04-subagent` | 委派子代理 | `subagent(role, task)` 隔离上下文 |
| `05-teams` | 持久化团队 | `Agent` / `Team` 类 + `inbox` 通信 |
| `06-compact` | 长会话自动压缩 | `compact_messages()` + `find_recent_start()` 防截断 |
| `07-safety` | 三道安全防线 | 命令黑名单 / 用户确认 / 输出截断 |

---

## 目录结构

```
nanoAgent/
├── README.md                    # 本文件
├── .agent/                      # 可插拔的外部能力配置（被 03/05 加载）
│   ├── mcp.json                 #   MCP 工具清单
│   ├── rules/
│   │   └── demo-style.md        #   输出风格规则（Rule）
│   └── skills/
│       └── release-triage/
│           └── SKILL.md         #   发布前问题排序 Skill
│
├── 01-essence/                  # 1. 最简 Agent 循环
│   └── agent-essence.py
│
├── 02-memory/                   # 2. + 持久记忆
│   ├── agent-memory.py
│   ├── agent_memory.md          #   自动生成的记忆文件
│   └── launch-note.txt          #   演示产物
│
├── 03-skills-mcp/               # 3. + Rules / Skills / MCP 工具
│   └── agent-skills-mcp.py
│
├── 04-subagent/                 # 4. + Subagent 委派
│   ├── agent-subagent.py
│   └── agent_memory.md
│
├── 05-teams/                    # 5. + 持久化 Team 编排
│   └── agent-teams.py
│
├── 06-compact/                  # 6. + 上下文自动压缩
│   ├── agent-compact.py
│   └── compact-demo-report.txt  #   演示产物（演进对比报告）
│
└── 07-safety/                   # 7. + 三道安全防线
    └── agent-safe.py
```

---

## 环境与运行

### 依赖

```bash
pip install openai httpx
```

### 环境变量

所有脚本通过 `OPENAI_*` 系列环境变量配置模型客户端（与 OpenAI Python SDK 兼容，可指向任何兼容端点）：

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `OPENAI_API_KEY` | ✅ | API 密钥 |
| `OPENAI_BASE_URL` | ✅ | 兼容端点 Base URL |
| `OPENAI_MODEL` | ⬜ | 模型名，默认 `MiniMax-M3` |

> Windows 端默认使用 `powershell.exe`；代码内所有 `subprocess.run(..., shell=True)` 与 `grep -r` 调用在 Git Bash / WSL / PowerShell 下均兼容（PowerShell 自带 `grep` 别名）。

### 运行方式

每个目录都是一个独立可运行的入口，直接 `python` 即可看到演示：

```bash
python 01-essence/agent-essence.py
python 02-memory/agent-memory.py
python 03-skills-mcp/agent-skills-mcp.py
python 04-subagent/agent-subagent.py
python 05-teams/agent-teams.py
python 06-compact/agent-compact.py
python 07-safety/agent-safe.py
```

部分脚本会在 `__main__` 中固化一个**演示任务**（见下文「演示任务速查」）；运行 `07-safety` 时会在每次 `bash`/`read`/`write` 前弹出交互式确认（`Y` 放行 / `N` 跳过 / `Q` 终止），设置 `AUTO_APPROVE = True` 可跳过。

---

## 演进路线详解

### 1. `01-essence` — 最简 Agent 循环

- 3 个工具：`execute_bash` / `read_file` / `write_file`
- `run_agent(user_message, max_iterations=5)`：`while` 循环里反复调用 `client.chat.completions.create(tools=...)`，分发 `tool_calls`，把工具结果以 `tool` 角色塞回 `messages`
- **缺点**：每轮把全量 `messages` 重新发出去，对话越长 token 越多；没有跨会话记忆

### 2. `02-memory` — 持久记忆

新增两个机制：

- `load_memory()`：从 `agent_memory.md` 读最近 50 行，拼进 system prompt
- `save_memory(task, result)`：每轮收尾时把 Task / Result 追加写回 markdown

> 本质是**跨会话**延续上下文，但**单次会话**内的 token 膨胀仍未解决 —— 这正是第 6 步要处理的问题。

### 3. `03-skills-mcp` — Rules / Skills / MCP

在 `base_tools` 之外，运行时从 `.agent/` 加载三类外部能力，并把它们**注入到 system prompt**：

| 来源 | 加载函数 | 注入方式 |
| --- | --- | --- |
| `.agent/rules/*.md` | `load_rules()` | `# Rules` 段 |
| `.agent/skills/*/SKILL.md` | `load_skills()` | `# Skills` 段（解析 frontmatter） |
| `.agent/mcp.json` | `load_mcp_tools()` | 合并到 `tools` 列表里 |

演示任务会同时调用：

- `release_triage` **Skill**（决定输出顺序）
- `demo_style` **Rule**（决定输出格式：固定 3 行）
- `demo_release_policy` **MCP 工具**（提供发布策略原文）

### 4. `04-subagent` — 委派子代理

新增一个工具 `subagent(role, task)`：

- 在 `subagent()` 内部启动一个**全新、独立**的 `messages` 列表和独立的 10 轮循环
- 子代理被**禁止**再调用 `subagent`（防无限递归）
- 子代理对主代理来说**只是一个字符串返回值** —— 上下文隔离，token 不互相污染

适合"任务需要专门知识 / 上下文应当隔离"的场景。

### 5. `05-teams` — 持久化团队

从"一次性函数调用"升级到**有状态的 `Agent` 对象**：

- **`Agent`**：有自己的 `name` / `role` / `messages`（跨 `chat()` 持久）/ `inbox`（收件箱）
- **`Team`**：管理生命周期 —— `hire()` → 多次 `chat()` + `send()` / `broadcast()` → `disband()`
- 每个 Agent 在新一轮 `chat()` 时会先把 `inbox` 里的团队消息消化掉，再处理任务
- 演示任务是一个**固定 3 人发布评审团队**（API 开发 / 安全审查 / 发布评审），最后由 reviewer 用 `G1/G2/G3` 验收标准做二次审查

### 6. `06-compact` — 上下文自动压缩

核心新增 `compact_messages()`：

```
压缩前: [system, msg1, msg2, ..., msg8]
压缩后: [system, 摘要(msg1..msgN), ack, msgM, msgM+1, msgM+2]
```

关键点：

- **阈值触发**：`COMPACT_THRESHOLD = 8`，演示用低阈值方便观察
- **保留尾部**：`KEEP_RECENT = 4`，至少留 4 条原样
- **防 tool 截断**：`find_recent_start()` 从后往前扫描，保证不会把 `assistant.tool_calls` / `tool` 响应对从中间切开
- **摘要生成**：把旧消息拼成文本再调一次 LLM 压缩

每次 `run_agent` 循环开头都先 `compact_messages(messages)`，再发请求。

### 7. `07-safety` — 三道安全防线

| 防线 | 实现 | 说明 |
| --- | --- | --- |
| ① 命令黑名单 | `is_dangerous()` + 正则 | `rm -rf /` / `mkfs` / `dd of=/dev/sda` / fork bomb / `curl \| sh` / `shutdown` / `chmod 777 /` 等 |
| ② 用户确认 | `ask_user_confirmation()` | 每次 `bash` / `read` / `write` 前交互式 `Y/N/Q`；`AUTO_APPROVE=True` 跳过 |
| ③ 输出截断 | `truncate_output()` | 超过 `MAX_OUTPUT_LENGTH=5000` 字符时保留首尾各一半 |

> 这三道防线**互相独立**：黑名单命中直接拒绝；未命中走确认；确认通过后结果再截断。

---

## 演示任务速查

| 目录 | 入口默认任务 |
| --- | --- |
| `01-essence` | 创建 `hello.txt`，内容是 `Hello Agent` |
| `02-memory` | 创建 `launch-note.txt`；再问"上一次做了什么"（验证记忆） |
| `03-skills-mcp` | 调用 `demo_release_policy` MCP 工具，按 `release_triage` Skill 给 3 个发布前问题排序，并按 `demo-style` Rule 输出 3 行 |
| `04-subagent` | 委派 2 个子代理（Python API 设计师 + 前端交互设计师），主代理纯文本 4 行汇总 |
| `05-teams` | 固定 3 人发布评审团队（alice / bob / chris）评审登录接口，最后由 chris 用 G1/G2/G3 做二次审查 |
| `06-compact` | 触发多轮 `bash` + `read_file` + `write_file`，观察压缩日志 |
| `07-safety` | "列出当前目录的文件"，体验三道防线（黑名单 / 确认 / 截断） |

---

## 设计原则

1. **单文件可运行**：每个目录是一个独立 `python xxx.py` 即可演示的最小完整例子，没有跨目录 import。
2. **不重写循环**：上层目录只是"在原循环里加一段"，例如 `06-compact` 只多了一个 `messages = compact_messages(messages)` 调用。
3. **可插拔的外部层**：`.agent/` 把"知识（Rules / Skills）"与"工具（MCP）"从代码中剥离，便于同一套 Agent 复用不同的领域配置。
4. **token 控制三段式**：源头截断（`07-safety` 的 `truncate_output`）+ 长会话压缩（`06-compact` 的 `compact_messages`）+ 跨会话延续（`02-memory` 的 `MEMORY.md`）。
5. **上下文隔离**：委派走 `subagent`、团队走 `Agent.inbox`，避免主线程 `messages` 无限膨胀。

---

## 已知边界

- 所有 Agent 在同一个 Python 进程内串行执行；并发 / 分布式需要外部调度。
- 演示以**单模型单端点**为主，摘要与主对话使用同一个模型（`OPENAI_MODEL`）；`06-compact` 的本地摘要思路是参考性的，未实际切到本地模型。
- `07-safety` 是**用户态**防线（黑名单 + 人工确认 + 输出截断），不是沙箱；真实高危场景仍需操作系统级隔离（容器 / seccomp / 权限收敛）。
- `.agent/mcp.json` 是**演示用工具清单**，不连接真实 MCP Server；工具实现 `demo_release_policy` 直接写在 `agent-skills-mcp.py` 里。

---

## 许可证

仓库未声明 License，使用前请联系作者确认。