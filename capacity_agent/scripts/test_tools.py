"""Test tool calling with volcano engine"""
import json
import httpx

# Simple tool schema for testing
SIMPLE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_tool_group_status",
            "description": "查询机台组实时状态,包含设备数、可用数、OEE等",
            "parameters": {
                "type": "object",
                "properties": {
                    "area": {"type": "string", "description": "区域筛选,如 LITHO, ETCH"},
                    "time_range": {"type": "string", "default": "current"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "compute_rccp_simple",
            "description": "简化版 RCCP 产能计算 - 自动获取数据并计算。推荐LLM使用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "time_window": {
                        "type": "string",
                        "description": "时间窗口: this_week | next_week",
                        "default": "this_week"
                    },
                    "area": {"type": "string", "description": "可选区域筛选"}
                },
                "required": []
            }
        }
    }
]

url = 'https://ark.cn-beijing.volces.com/api/v3/chat/completions'
headers = {
    'Authorization': 'Bearer 84fae4ea-f780-4c80-afcb-351d650b2da1',
    'Content-Type': 'application/json'
}

payload = {
    'model': 'ep-20260203172510-zzmqg',
    'messages': [
        {'role': 'system', 'content': '你是一个产能分析助手，请调用工具获取数据回答问题。'},
        {'role': 'user', 'content': '本周产能现况如何？请使用 compute_rccp_simple 工具计算'}
    ],
    'tools': SIMPLE_TOOLS,
    'tool_choice': 'auto'
}

print('Tools count:', len(SIMPLE_TOOLS))
print('Schema JSON size:', len(json.dumps(SIMPLE_TOOLS)))

resp = httpx.post(url, headers=headers, json=payload, timeout=60)
print('Status:', resp.status_code)
data = resp.json()

if 'choices' in data:
    msg = data['choices'][0]['message']
    print('Content preview:', msg.get('content', '')[:200] if msg.get('content') else None)
    print('Tool_calls:', msg.get('tool_calls'))
else:
    print('Error:', data)