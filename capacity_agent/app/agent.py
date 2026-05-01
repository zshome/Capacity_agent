"""
Capacity Agent (LangGraph state machine)
========================================

LLM ←→ Tool Router 循环,直到 LLM 决定不再调用工具,生成最终回答。

设计要点:
1. 强制 tool-use:LLM 第一轮必须至少调用一个工具 (在 prompt 中要求)
2. 审计日志: 每次 tool 调用都落库 (audit_log 表)
3. 防失控: 单次会话最多 8 轮 tool call,超出则强制结束

依赖: pip install langgraph
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.llm_client import get_llm_client
from app.prompts import get_system_prompt
from app.tools import TOOL_FUNCTIONS, SIMPLIFIED_TOOL_SCHEMAS as TOOL_SCHEMAS

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 8


# ============================================================
# Agent State
# ============================================================
class AgentState(TypedDict):
    session_id: str
    messages: list[dict[str, Any]]          # OpenAI message list
    tool_calls_log: list[dict[str, Any]]    # for audit
    iterations: int
    final_answer: str | None


# ============================================================
# Nodes
# ============================================================
def llm_node(state: AgentState) -> AgentState:
    """LLM 决策: 是否调用工具,或给出最终答案"""
    llm = get_llm_client()
    
    # DEBUG: Log tools being passed
    logger.info(f"LLM node: passing {len(TOOL_SCHEMAS)} tools to LLM")

    response = llm.chat(
        messages=state["messages"],
        tools=TOOL_SCHEMAS,
        temperature=0.1,
    )
    
    # DEBUG: Log response
    logger.info(f"LLM response: tool_calls={len(response.get('tool_calls') or [])}")

    assistant_msg: dict[str, Any] = {"role": "assistant"}
    if response["content"]:
        assistant_msg["content"] = response["content"]
    if response["tool_calls"]:
        assistant_msg["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                },
            }
            for tc in response["tool_calls"]
        ]

    state["messages"].append(assistant_msg)

    if not response["tool_calls"]:
        state["final_answer"] = response["content"] or ""

    return state


def tool_node(state: AgentState) -> AgentState:
    """执行 LLM 请求的所有工具调用"""
    last_msg = state["messages"][-1]
    tool_calls = last_msg.get("tool_calls", [])

    for tc in tool_calls:
        tool_name = tc["function"]["name"]
        try:
            args = json.loads(tc["function"]["arguments"])
        except json.JSONDecodeError:
            args = {}

        t0 = time.time()
        try:
            fn = TOOL_FUNCTIONS.get(tool_name)
            if fn is None:
                result = {"error": f"Unknown tool: {tool_name}"}
            else:
                result = fn(**args)
            success = "error" not in result
        except Exception as e:
            logger.exception(f"Tool {tool_name} raised")
            result = {"error": str(e)}
            success = False

        elapsed = time.time() - t0

        # 审计日志
        state["tool_calls_log"].append({
            "session_id": state["session_id"],
            "iteration": state["iterations"],
            "tool_name": tool_name,
            "arguments": args,
            "success": success,
            "elapsed_seconds": round(elapsed, 3),
            "result_preview": str(result)[:500],
        })

        # 把结果反馈给 LLM
        state["messages"].append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "name": tool_name,
            "content": json.dumps(result, ensure_ascii=False)[:8000],   # 截断超长结果
        })

    state["iterations"] += 1
    return state


def should_continue(state: AgentState) -> str:
    """路由: 是继续调用工具还是结束"""
    if state["final_answer"] is not None:
        return "end"
    if state["iterations"] >= MAX_TOOL_ITERATIONS:
        # 强制结束: 让 LLM 根据已有信息给出最终回答
        state["messages"].append({
            "role": "user",
            "content": "已达到工具调用上限,请根据当前已收集的信息给出最终回答。",
        })
        return "llm"
    return "tools"


# ============================================================
# Build graph
# ============================================================
def build_agent_graph():
    g = StateGraph(AgentState)
    g.add_node("llm", llm_node)
    g.add_node("tools", tool_node)

    g.set_entry_point("llm")
    g.add_conditional_edges("llm", should_continue, {
        "tools": "tools",
        "llm": "llm",
        "end": END,
    })
    g.add_edge("tools", "llm")

    return g.compile()


# ============================================================
# Public API
# ============================================================
@dataclass
class AgentResponse:
    session_id: str
    answer: str
    tool_calls: list[dict[str, Any]]
    iterations: int
    elapsed_seconds: float


_compiled_graph = None


def get_agent():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_agent_graph()
    return _compiled_graph


def query_agent(user_query: str, session_id: str | None = None) -> AgentResponse:
    """主入口: 用户提问 → Agent 回答"""
    sid = session_id or str(uuid.uuid4())
    t0 = time.time()

    initial_state: AgentState = {
        "session_id": sid,
        "messages": [
            {"role": "system", "content": get_system_prompt()},
            {"role": "user", "content": user_query},
        ],
        "tool_calls_log": [],
        "iterations": 0,
        "final_answer": None,
    }

    agent = get_agent()
    final = agent.invoke(initial_state, config={"recursion_limit": MAX_TOOL_ITERATIONS * 3 + 5})

    return AgentResponse(
        session_id=sid,
        answer=final.get("final_answer") or "(no answer)",
        tool_calls=final["tool_calls_log"],
        iterations=final["iterations"],
        elapsed_seconds=round(time.time() - t0, 2),
    )
