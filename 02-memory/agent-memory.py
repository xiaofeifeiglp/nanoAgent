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

MEMORY_FILE = "agent_memory.md"

tools = [
    {
        "type": "function",
        "function": {
            "name": "execute_bash",
            "description": "Execute a bash command",
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
            "name": "read_file",
            "description": "Read a file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write to a file",
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
]


def execute_bash(command):
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    return result.stdout + result.stderr


def read_file(path):
    with open(path, "r") as f:
        return f.read()


def write_file(path, content):
    with open(path, "w") as f:
        f.write(content)
    return f"Wrote to {path}"


functions = {"execute_bash": execute_bash, "read_file": read_file, "write_file": write_file}


def load_memory():
    if not os.path.exists(MEMORY_FILE):
        return ""
    with open(MEMORY_FILE, "r", encoding="utf8") as f:
        lines = f.read().splitlines()
    return "\n".join(lines[-50:])


def save_memory(task, result):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"\n## {timestamp}\n**Task:** {task}\n**Result:** {result}\n"
    with open(MEMORY_FILE, "a", encoding="utf8") as f:
        f.write(entry)
    print(f"[Memory] Saved to {MEMORY_FILE}")


def build_messages(user_message):
    system_prompt = "You are a helpful assistant. Be concise."
    memory = load_memory()
    if memory:
        print(f"[Memory] Loaded {MEMORY_FILE}")
        system_prompt += f"\n\nPrevious context:\n{memory}"
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]


def send_messages(messages):
    """发送消息并返回响应"""
    response = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "MiniMax-M3"),
        messages=messages,
        tools=tools,
        # 设置 reasoning_split=True 将思考内容分离到 reasoning_details 字段
        extra_body={"reasoning_split": True},
    )
    return response.choices[0].message


def run_agent(user_message, max_iterations=5):
    messages = build_messages(user_message)
    for _ in range(max_iterations):
        response_message = send_messages(messages)
        messages.append(response_message)
        if not response_message.tool_calls:
            save_memory(user_message, response_message.content)
            return response_message.content
        for tool_call in response_message.tool_calls:
            name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            print(f"[Tool] {name}({args})")
            if name not in functions:
                result = f"Error: Unknown tool '{name}'"
            else:
                result = functions[name](**args)
            messages.append(
                {"role": "tool", "tool_call_id": tool_call.id, "content": result}
            )
    result = "Max iterations reached"
    save_memory(user_message, result)
    return result


if __name__ == "__main__":
    # python agent-memory.py "创建 launch-note.txt，内容是 Agent Memory Demo"
    # python agent-memory.py "不重新读文件，只根据记忆说明你上一次完成了什么任务"
    task1 = "创建 launch-note.txt，内容是 Agent Memory Demo"
    task1 = "不重新读文件，只根据记忆说明你上一次完成了什么任务"
    print(run_agent(task1))
