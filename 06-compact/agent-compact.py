import os
import json
import subprocess
import sys
import httpx
from openai import OpenAI

client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY"),
    base_url=os.environ.get("OPENAI_BASE_URL"),
    http_client=httpx.Client(verify=False),
)

# ==================== 工具（和 agent-essence.py 一样） ====================
tools = [
    {
        "type": "function",
        "function": {
            "name": "execute_bash",
            "description": "Execute a bash command on the system",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute",
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read contents of a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file"},
                    "content": {"type": "string", "description": "Content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
]


def execute_bash(command):
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        return result.stdout + result.stderr
    except Exception as e:
        return f"Error: {str(e)}"


def read_file(path):
    try:
        with open(path, "r", encoding="utf8") as f:
            return f.read()
    except Exception as e:
        return f"Error: {str(e)}"


def write_file(path, content):
    try:
        with open(path, "w", encoding="utf8") as f:
            f.write(content)
        return f"Successfully wrote to {path}"
    except Exception as e:
        return f"Error: {str(e)}"


available_functions = {
    "execute_bash": execute_bash,
    "read_file": read_file,
    "write_file": write_file,
}

# ==================== 上下文压缩（核心新增） ====================
#
# 核心是 compact_messages()；find_recent_start() 用来避免切断 tool 调用组。
#
# 原理：
#   messages = [system, user, assistant, tool, assistant, tool, ...]
#                 ↓ 压缩后
#   messages = [system, 摘要(包含之前所有要点), 最近 N 条消息]

COMPACT_THRESHOLD = 8  # 演示用低阈值：几轮工具调用后就能看到压缩
KEEP_RECENT = 4  # 至少保留最近几条消息；遇到 tool 调用组会向前扩展


def message_role(message):
    if isinstance(message, dict):
        return message.get("role", "unknown")
    return getattr(message, "role", "unknown")


def find_recent_start(messages):
    start = max(1, len(messages) - KEEP_RECENT)
    # 不要从 tool 消息中间切开；tool 必须紧跟触发它的 assistant tool_call。
    while start > 1 and message_role(messages[start]) == "tool":
        start -= 1
    return start


def compact_messages(messages):
    """
    当 messages 过长时，把旧消息压缩成一段摘要。

    压缩前: [system, msg1, msg2, msg3, msg4, msg5, msg6, msg7, msg8]
    压缩后: [system, summary_of(msg1~msg4), ack, msg5, msg6, msg7, msg8]
    """
    if len(messages) <= COMPACT_THRESHOLD:
        return messages  # 没超阈值，不压缩

    print(
        f"\n[Compact] messages 数量 ({len(messages)}) 超过阈值 ({COMPACT_THRESHOLD})，开始压缩..."
    )

    system_msg = messages[0]  # system prompt 永远保留
    recent_start = find_recent_start(messages)
    old_messages = messages[1:recent_start]  # 需要被压缩的旧消息
    recent_messages = messages[recent_start:]  # 最近的消息保留原样

    # 把旧消息拼成文本，交给 LLM 做摘要
    old_text = ""
    for msg in old_messages:
        role = message_role(msg)
        content = (
            msg.get("content", "")
            if isinstance(msg, dict)
            else getattr(msg, "content", "")
        )
        if content:
            old_text += f"[{role}]: {content}\n"

    # 调用 LLM 生成摘要
    summary_response = client.chat.completions.create(
        messages=[
            {
                "role": "system",
                "content": "Summarize the following conversation history into a concise summary. Keep all important facts, file paths, command results, and decisions. Be concise but don't lose critical details.",
            },
            {"role": "user", "content": old_text},
        ],
        model=os.environ.get("OPENAI_MODEL", "MiniMax-M3"),
        # 设置 reasoning_split=True 将思考内容分离到 reasoning_details 字段
        extra_body={"reasoning_split": True},
    )
    summary = summary_response.choices[0].message.content

    print(
        f"[Compact] {len(old_messages)} 条旧消息 → 1 条摘要 (保留最近 {len(recent_messages)} 条)"
    )
    print(f"[Compact] 压缩后 messages: {1 + 2 + len(recent_messages)} 条\n")

    # 重新组装：system + 摘要 + 最近消息
    return [
        system_msg,
        {"role": "user", "content": f"[Previous conversation summary]: {summary}"},
        {
            "role": "assistant",
            "content": "Understood. I have the context from our previous conversation. Let me continue.",
        },
        *recent_messages,
    ]


# ==================== Agent 核心循环（在 agent-essence.py 基础上加了压缩） ====================

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


def run_agent(user_message, max_iterations=30):
    messages = [
        {
            "role": "system",
            "content": "You are a helpful assistant that can interact with the system. Be concise.",
        },
        {"role": "user", "content": user_message},
    ]

    for i in range(max_iterations):
        # ===== 新增：每轮循环前检查是否需要压缩 =====
        messages = compact_messages(messages)
        response_message = send_messages(messages, tools)
        messages.append(response_message)

        if not response_message.tool_calls:
            return response_message.content

        for tool_call in response_message.tool_calls:
            function_name = tool_call.function.name
            function_args = json.loads(tool_call.function.arguments)
            print(
                f"[Tool] {function_name}({json.dumps(function_args, ensure_ascii=False)[:80]})"
            )
            function_response = available_functions[function_name](**function_args)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": function_response,
                }
            )

    return "Max iterations reached"


# ==================== 主入口 ====================

if __name__ == "__main__":
    task = "请按步骤执行，不要合并成一个 shell 命令：1. 用 execute_bash 列出 agent 目录下的 Python 文件；2. 分别用 read_file 读取 nanoAgent/01-essence/agent-essence.py、nanoAgent/02-memory/agent-memory.py、nanoAgent/06-compact/agent-compact.py；3. 把三个文件的大致行数写入 nanoAgent/06-compact/compact-demo-report.txt。"
    result = run_agent(task)
    print(f"\n{result}")
