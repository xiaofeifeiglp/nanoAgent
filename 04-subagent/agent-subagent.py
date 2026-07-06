import os
import json
import subprocess
import sys
import glob as glob_module
import httpx
from datetime import datetime
from openai import OpenAI

client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY"),
    base_url=os.environ.get("OPENAI_BASE_URL"),
    http_client=httpx.Client(verify=False),
)

MEMORY_FILE = "agent_memory.md"

# ==================== 工具实现 ====================


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


def glob(pattern):
    try:
        files = sorted(
            glob_module.glob(pattern, recursive=True),
            key=lambda x: os.path.getmtime(x),
            reverse=True,
        )
        return "\n".join(files) if files else "No files found"
    except Exception as e:
        return f"Error: {str(e)}"


def grep(pattern, path="."):
    try:
        result = subprocess.run(
            f"grep -rn '{pattern}' {path}",
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout if result.stdout else "No matches found"
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


# ==================== SubAgent 实现（核心） ====================


def subagent(role, task):
    """启动一个独立的 Agent 循环，拥有专属角色和独立上下文"""
    print(f"\n{'=' * 50}")
    print(f"[SubAgent:{role}] 开始: {task}")
    print(f"{'=' * 50}")

    sub_messages = [
        {
            "role": "system",
            "content": f"You are a {role}. Be concise and focused. Only do what is asked.",
        },
        {"role": "user", "content": task},
    ]
    # SubAgent 不能再派 subagent（防无限递归），只用基础工具
    sub_tools = [t for t in tools if t["function"]["name"] != "subagent"]

    for _ in range(10):
        response_message = send_messages(sub_messages, tools)
        sub_messages.append(response_message)

        if not response_message.tool_calls:
            print(f"[SubAgent:{role}] 完成\n")
            return response_message.content

        for tc in response_message.tool_calls:
            fn = tc.function.name
            args = json.loads(tc.function.arguments)
            print(
                f"  [SubAgent:{role}] {fn}({json.dumps(args, ensure_ascii=False)[:80]})"
            )
            result = available_functions[fn](**args)
            sub_messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result}
            )

    return "SubAgent: max iterations reached"


# ==================== 工具注册 ====================

available_functions = {
    "read": read,
    "write": write,
    "edit": edit,
    "glob": glob,
    "grep": grep,
    "bash": bash,
    "subagent": subagent,
}

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
            "name": "glob",
            "description": "Find files by pattern",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search files for pattern",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["pattern"],
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
    {
        "type": "function",
        "function": {
            "name": "subagent",
            "description": "Delegate a task to a specialized sub-agent with its own role and independent context. Use this when a task requires specific expertise (e.g. 'frontend developer', 'DBA', 'test engineer').",
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "description": "The sub-agent's specialty, e.g. 'Python backend developer'",
                    },
                    "task": {
                        "type": "string",
                        "description": "The specific task to delegate",
                    },
                },
                "required": ["role", "task"],
            },
        },
    },
]

# ==================== 记忆 ====================


def load_memory():
    if not os.path.exists(MEMORY_FILE):
        return ""
    try:
        with open(MEMORY_FILE, "r", encoding="utf8") as f:
            lines = f.read().split("\n")
        return "\n".join(lines[-50:]) if len(lines) > 50 else "\n".join(lines)
    except:
        return ""


def save_memory(task, result):
    try:
        with open(MEMORY_FILE, "a", encoding="utf8") as f:
            f.write(
                f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n**Task:** {task}\n**Result:** {result}\n"
            )
    except:
        pass


# ==================== Agent 核心循环 ====================


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


def run_agent(messages, max_iterations=10):
    for _ in range(max_iterations):
        response_message = send_messages(messages, tools)
        messages.append(response_message)

        if not response_message.tool_calls:
            return response_message.content

        for tc in response_message.tool_calls:
            fn = tc.function.name
            args = json.loads(tc.function.arguments)
            print(f"[Tool] {fn}({json.dumps(args, ensure_ascii=False)[:100]})")
            result = available_functions.get(fn, lambda **_: f"Tool {fn} not found")(
                **args
            )
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    return "Max iterations reached"


# ==================== 主入口 ====================


def run(task):
    memory = load_memory()
    system = "You are an orchestrator agent. You can do tasks yourself OR delegate to specialized sub-agents using the 'subagent' tool. Use subagent when a task benefits from focused expertise. Be concise."
    if memory:
        system += f"\n\n# Previous Context\n{memory}"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]
    result = run_agent(messages)
    print(f"\n{result}")
    save_memory(task, result)
    return result


if __name__ == "__main__":
    task = "不要直接完成任务。请调用 subagent 工具两次，两个子代理都不要读写文件：1）role=Python API 设计师，task=为 TODO 应用设计 3 个后端接口，只返回接口清单；2）role=前端交互设计师，task=为 TODO 应用设计 3 个界面交互，只返回交互清单。最后主 Agent 用纯文本 4 行汇总，不要表格：后端交付、前端交付、为什么适合委派、主 Agent 没做什么。"
    run(task)
