import os
import json
import subprocess
import sys
import httpx
from datetime import datetime
from openai import OpenAI

client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY"),
    base_url=os.environ.get("OPENAI_BASE_URL"),
    http_client=httpx.Client(verify=False),
)


# ==================== 工具 ====================

def read(path, offset=None, limit=None):
    try:
        with open(path, "r") as f:
            lines = f.readlines()
        start = offset if offset else 0
        end = start + limit if limit else len(lines)
        return "".join(
            f"{i + 1:4d} {line}" for i, line in enumerate(lines[start:end], start)
        )
    except Exception as e:
        return f"Error: {str(e)}"


def write(path, content):
    try:
        os.makedirs(
            os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True
        )
        with open(path, "w") as f:
            f.write(content)
        return f"Successfully wrote to {path}"
    except Exception as e:
        return f"Error: {str(e)}"


def edit(path, old_string, new_string):
    try:
        with open(path, "r") as f:
            content = f.read()
        if content.count(old_string) != 1:
            return f"Error: old_string must appear exactly once (found {content.count(old_string)})"
        with open(path, "w") as f:
            f.write(content.replace(old_string, new_string))
        return f"Successfully edited {path}"
    except Exception as e:
        return f"Error: {str(e)}"


def bash(command):
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        return result.stdout + result.stderr
    except Exception as e:
        return f"Error: {str(e)}"


available_functions = {"read": read, "write": write, "edit": edit, "bash": bash}

tools = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read file with line numbers",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Write content to file (creates dirs automatically)",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "Replace a unique string in file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run shell command",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
]


# ==================== 核心 1: 持久智能体（Agent 类） ====================
#
# 对比 SubAgent（一个函数调用就消亡），Agent 是一个有状态的对象:
#   - 有名字（name）和角色（role）—— 身份
#   - 有 messages 列表 —— 记忆，跨多次 chat() 调用持久保持
#   - 有 inbox —— 通信通道，接收其他 Agent 发来的消息


class Agent:
    def __init__(self, name, role):
        self.name = name
        self.role = role
        self.inbox = []  # 通信通道：其他 Agent 发来的消息
        self.chat_count = 0
        self.messages = [  # 持久记忆：跨多次 chat() 保持
            {
                "role": "system",
                "content": f"You are {name}, a {role}. Be concise and focused.",
            }
        ]
        print(f"  [创建] {name} ({role})")

    def receive(self, sender, message):
        """核心 3: 通信通道 —— 接收来自其他 Agent 的消息"""
        self.inbox.append({"from": sender, "content": message})

    def chat(self, task):
        """
        核心 1: 持久记忆 —— 每次 chat() 的对话都累积在 self.messages 中
        第二次 chat() 时，Agent 还记得第一次做了什么
        """
        self.chat_count += 1
        print(
            f"  [记忆] {self.name} 第 {self.chat_count} 次 chat，"
            f"已有 {len(self.messages)} 条 messages，inbox {len(self.inbox)} 条"
        )
        # 如果 inbox 有新消息，先注入
        if self.inbox:
            print(f"  [收件箱] {self.name} 读取 {len(self.inbox)} 条团队消息")
            mail = "\n".join(f"[来自 {m['from']}]: {m['content']}" for m in self.inbox)
            self.messages.append(
                {"role": "user", "content": f"你收到了团队成员的消息:\n{mail}"}
            )
            # 让 Agent 先消化这些消息
            resp_message = send_messages(self.messages, tools)
            self.messages.append(resp_message)
            self.inbox.clear()

        # 执行本次任务
        self.messages.append({"role": "user", "content": task})

        for _ in range(10):
            response_message = send_messages(self.messages, tools)

            self.messages.append(response_message)

            if not response_message.tool_calls:
                print(f"  [{self.name}] → {response_message.content[:100]}...")
                print(
                    f"  [记忆] {self.name} 本轮结束，messages {len(self.messages)} 条"
                )
                return response_message.content

            for tc in response_message.tool_calls:
                fn = tc.function.name
                args = json.loads(tc.function.arguments)
                print(
                    f"  [{self.name}] {fn}({json.dumps(args, ensure_ascii=False)[:60]})"
                )
                result = available_functions.get(fn, lambda **_: "Tool not found")(
                    **args
                )
                self.messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result}
                )

        return "Max iterations reached"


# ==================== 核心 2: 身份与生命周期管理（Team 类） ====================
#
# Team 管理 Agent 的完整生命周期:
#   创建（hire）→ 存活（多次 chat + 互相通信）→ 解散（disband）


class Team:
    def __init__(self):
        self.agents = {}  # name → Agent

    def hire(self, name, role):
        """招募：创建一个持久 Agent"""
        agent = Agent(name, role)
        self.agents[name] = agent
        return agent

    def send(self, from_name, to_name, message):
        """核心 3: Agent 之间的通信通道"""
        if to_name not in self.agents:
            return f"Error: {to_name} not found"
        self.agents[to_name].receive(from_name, message)
        print(f"  [消息] {from_name} → {to_name}: {message[:60]}...")

    def broadcast(self, from_name, message):
        """广播：给团队所有其他人发消息"""
        for name, agent in self.agents.items():
            if name != from_name:
                agent.receive(from_name, message)
        print(f"  [广播] {from_name} → 全体: {message[:60]}...")

    def disband(self):
        """解散：所有 Agent 生命周期结束"""
        names = list(self.agents.keys())
        self.agents.clear()
        print(f"  [解散] 团队已解散 ({', '.join(names)})")


# ==================== 团队编排 ====================

def send_messages(messages, tools_list):
    """发送消息并返回响应"""
    response = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "MiniMax-M3"),
        messages=messages,
        tools=tools_list,
        # 设置 reasoning_split=True 将思考内容分离到 reasoning_details 字段
        extra_body={"reasoning_split": True},
    )
    return response.choices[0].message


def plan_team(task):
    """让 LLM 根据任务规划团队成员"""
    if "固定 3 人发布评审团队" in task:
        return [
            {
                "name": "alice",
                "role": "api developer",
                "task": "不要读写文件。只输出 3 行登录接口交付摘要，每行不超过 18 个字。",
            },
            {
                "name": "bob",
                "role": "security reviewer",
                "task": "不要读写文件。只输出 2 行安全风险与建议，每行不超过 24 个字。",
            },
            {
                "name": "chris",
                "role": "release reviewer",
                "task": "不要读写文件。先输出 3 行发布验收标准，编号为 G1/G2/G3，每行不超过 18 个字。",
            },
        ]

    print(f"\n[PM] 分析任务，组建团队...")
    messages = [
        {
            "role": "system",
            "content": """You are a project manager. Given a task, plan a team of 2-4 members.
Return JSON: {"team": [{"name": "alice", "role": "...", "task": "..."}]}
Rules: use lowercase english names, last member should be a reviewer, keep tasks concise.""",
        },
        {"role": "user", "content": task},
    ]
    response_message = send_messages(messages, tools)

    try:
        return json.loads(response_message.content).get("team", [])
    except:
        return [{"name": "dev", "role": "developer", "task": task}]


def run_team(task):
    """
    完整的团队协作流程，展示三个核心能力:

    1. 持久记忆 —— 同一个 Agent 被多次 chat()，记得之前做过什么
    2. 身份生命周期 —— hire() 创建 → 多次交互 → disband() 解散
    3. 通信通道 —— Agent 之间通过 send()/broadcast() 传递信息
    """
    team = Team()
    is_fixed_demo = "固定 3 人发布评审团队" in task

    # ---- 第 1 阶段：组建团队 ----
    members = plan_team(task)
    print(f"\n[团队] {len(members)} 人:")
    for i, m in enumerate(members, 1):
        print(f"  {i}. {m['name']} — {m['role']} → {m['task']}")

    print(f"\n{'=' * 60}")
    print("  第 1 阶段: 招募团队")
    print(f"{'=' * 60}")
    for m in members:
        team.hire(m["name"], m["role"])

    # ---- 第 2 阶段：逐个执行，每人干完把成果广播给全队 ----
    print(f"\n{'=' * 60}")
    print("  第 2 阶段: 协作开发")
    print(f"{'=' * 60}")

    results = {}
    for i, m in enumerate(members):
        print(f"\n{'─' * 60}")
        print(f"  [{i + 1}/{len(members)}] {m['name']} 开始工作")
        print(f"{'─' * 60}")

        agent = team.agents[m["name"]]
        result = agent.chat(m["task"])
        results[m["name"]] = result

        # 干完活，把成果广播给团队其他人
        team.broadcast(m["name"], f"我完成了任务。摘要: {result[:200]}")

    # ---- 第 3 阶段（可选）：让最后一个成员做二次审查 ----
    # 这里展示"持久记忆"的价值：reviewer 已经通过 inbox 收到了所有人的成果
    # 再次 chat() 时，他还记得之前收到的所有广播消息
    last = members[-1]
    reviewer = team.agents[last["name"]]

    print(f"\n{'=' * 60}")
    print(f"  第 3 阶段: {last['name']} 做最终审查")
    print(f"{'=' * 60}")

    review_prompt = (
        "请引用你第一次制定的 G1/G2/G3 验收标准，并根据你收到的团队成果，用 4 行输出最终审查：结论、记忆证据、风险、下一步。不要表格。"
        if is_fixed_demo
        else "请根据你收到的所有团队成果，做一个最终的总结和审查。如有问题请指出。"
    )
    review = reviewer.chat(review_prompt)
    results["final_review"] = review

    # ---- 解散 ----
    print(f"\n{'=' * 60}")
    print("  第 4 阶段: 解散团队")
    print(f"{'=' * 60}")
    team.disband()

    # ---- 输出 ----
    print(f"\n{'=' * 60}")
    print("  最终成果")
    print(f"{'=' * 60}\n")
    for name, result in results.items():
        print(f"[{name}]")
        print(f"  {result[:300]}\n")

    return results


# ==================== 主入口 ====================

if __name__ == "__main__":
    task = "固定 3 人发布评审团队演示：登录接口发布前评审"
    run_team(task)
