"""
Capacity Agent Main Service
============================

FastAPI app that exposes /agent/query endpoint.
Run:
  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.agent import query_agent
from app.llm_client import get_llm_client, reset_llm_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


app = FastAPI(
    title="Capacity Agent API",
    version="1.0.0",
    description="存储芯片厂产能现况 Agent 服务",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Endpoints
# ============================================================
class QueryRequest(BaseModel):
    query: str = Field(..., description="自然语言查询")
    session_id: str | None = Field(None, description="会话 ID,留空自动生成")


class LLMConfigRequest(BaseModel):
    provider: str = Field(..., description="LLM provider: vllm/openai/volces/dashscope/custom")
    model: str | None = Field(None, description="模型名称")
    api_key: str | None = Field(None, description="API密钥")
    base_url: str | None = Field(None, description="自定义 base URL")


@app.get("/health")
def health():
    return {"status": "ok", "service": "capacity-agent", "version": app.version}


@app.post("/agent/query")
def agent_query(req: QueryRequest):
    """主查询接口"""
    try:
        result = query_agent(req.query, req.session_id)
        return {
            "session_id": result.session_id,
            "answer": result.answer,
            "iterations": result.iterations,
            "elapsed_seconds": result.elapsed_seconds,
            "tool_calls": [
                {
                    "tool_name": tc["tool_name"],
                    "elapsed_seconds": tc["elapsed_seconds"],
                    "success": tc["success"],
                }
                for tc in result.tool_calls
            ],
        }
    except Exception as e:
        logger.exception("agent_query failed")
        status_code = 503 if "connection" in str(e).lower() or "timeout" in str(e).lower() else 500
        raise HTTPException(status_code, str(e))


@app.post("/agent/query_verbose")
def agent_query_verbose(req: QueryRequest):
    """完整调试输出 (含每次 tool call 的详细参数和结果)"""
    try:
        result = query_agent(req.query, req.session_id)
        return {
            "session_id": result.session_id,
            "answer": result.answer,
            "iterations": result.iterations,
            "elapsed_seconds": result.elapsed_seconds,
            "tool_calls": result.tool_calls,
        }
    except Exception as e:
        logger.exception("agent_query_verbose failed")
        status_code = 503 if "connection" in str(e).lower() or "timeout" in str(e).lower() else 500
        raise HTTPException(status_code, str(e))


@app.post("/llm/configure")
def configure_llm(req: LLMConfigRequest):
    """配置 Agent 使用的 LLM 连接参数"""
    defaults = {
        "vllm": "http://vllm:8000/v1",
        "openai": "https://api.openai.com/v1",
        "volces": "https://ark.cn-beijing.volces.com/api/v3",
        "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    }

    if req.provider in defaults:
        os.environ["LLM_BASE_URL"] = defaults[req.provider]
    elif req.base_url:
        base = req.base_url.rstrip("/").replace("/chat/completions", "").replace("/completions", "")
        os.environ["LLM_BASE_URL"] = base

    if req.model:
        os.environ["LLM_MODEL"] = req.model

    if req.api_key:
        os.environ["LLM_API_KEY"] = req.api_key

    reset_llm_client()
    client = get_llm_client()
    return {
        "status": "configured",
        "provider": req.provider,
        "model": client.model,
        "base_url": client.base_url,
        "message": "Agent LLM配置已更新"
    }


@app.get("/llm/status")
def llm_status():
    """获取 Agent 当前使用的 LLM 配置状态"""
    return {
        "base_url": os.getenv("LLM_BASE_URL", os.getenv("VLLM_BASE_URL", "http://vllm:8000/v1")),
        "model": os.getenv("LLM_MODEL", "Qwen/Qwen2.5-32B-Instruct"),
        "api_key_set": bool(os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")),
        "timeout": int(os.getenv("LLM_TIMEOUT", "120"))
    }
