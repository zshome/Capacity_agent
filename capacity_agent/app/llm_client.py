"""
LLM Client
==========

Supports multiple LLM backends via OpenAI-compatible API:

1. Local vLLM (default): http://vllm:8000/v1
2. OpenAI: https://api.openai.com/v1
3. Azure OpenAI: https://YOUR_RESOURCE.openai.azure.com/openai/deployments/YOUR_DEPLOYMENT
4. Volces/火山引擎: https://ark.cn-beijing.volces.com/api/v3
5. DashScope/通义千问: https://dashscope.aliyuncs.com/compatible-mode/v1
6. Custom: any OpenAI-compatible endpoint

Configuration via environment variables:
  - LLM_BASE_URL: API endpoint URL
  - LLM_MODEL: Model name/ID
  - LLM_API_KEY: API key (required for external services)
  - LLM_TIMEOUT: Request timeout in seconds (default 120)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)


# Environment variable configuration
LLM_BASE_URL = os.getenv("LLM_BASE_URL", os.getenv("VLLM_BASE_URL", "http://vllm:8000/v1"))
LLM_MODEL = os.getenv("LLM_MODEL", "Qwen/Qwen2.5-32B-Instruct")
LLM_API_KEY = os.getenv("LLM_API_KEY", os.getenv("OPENAI_API_KEY", "EMPTY"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "120"))


class LLMClient:
    """
    Unified OpenAI-compatible client supporting multiple backends
    
    Backends:
      - vLLM (local): base_url=http://vllm:8000/v1, api_key=EMPTY
      - OpenAI: base_url=https://api.openai.com/v1, api_key=sk-xxx
      - Volces: base_url=https://ark.cn-beijing.volces.com/api/v3, api_key=xxx
      - DashScope: base_url=https://dashscope.aliyuncs.com/compatible-mode/v1, api_key=sk-xxx
    """

    def __init__(
        self, 
        base_url: str | None = None, 
        model: str | None = None,
        api_key: str | None = None,
        timeout: int | None = None
    ):
        # 动态读取环境变量（不使用模块级常量）
        self.base_url = base_url or os.getenv("LLM_BASE_URL") or os.getenv("VLLM_BASE_URL") or "http://vllm:8000/v1"
        self.model = model or os.getenv("LLM_MODEL") or "Qwen/Qwen2.5-32B-Instruct"
        self._api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "EMPTY"
        self._timeout = timeout or int(os.getenv("LLM_TIMEOUT", "120"))
        
        self.client = OpenAI(
            base_url=self.base_url,
            api_key=self._api_key,
            timeout=self._timeout,
        )
        
        # Detect backend type for logging
        backend_type = "local vLLM" if "vllm" in self.base_url else "external API"
        logger.info(f"LLM client initialized: {backend_type} @ {self.base_url} model={self.model}")

    def chat(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        """
        Chat completion with optional tool calling
        
        Returns:
        {
          "content": str | None,
          "tool_calls": [{"id": str, "name": str, "arguments": dict}] | None,
          "finish_reason": str
        }
        """
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            resp = self.client.chat.completions.create(**kwargs)
            msg = resp.choices[0].message
            
            # DEBUG: Log raw response
            logger.debug(f"LLM response: content={msg.content[:100] if msg.content else None}, tool_calls={len(msg.tool_calls) if msg.tool_calls else 0}")

            tool_calls = None
            if msg.tool_calls:
                tool_calls = []
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        logger.warning(f"Bad JSON in tool args: {tc.function.arguments}")
                        args = {}
                    tool_calls.append({
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": args,
                    })

            return {
                "content": msg.content,
                "tool_calls": tool_calls,
                "finish_reason": resp.choices[0].finish_reason,
            }
        except Exception as e:
            logger.error(f"LLM API call failed: {e}")
            raise

    def simple_chat(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> str:
        """Simple chat without tool calling"""
        result = self.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return result.get("content") or ""


# Singleton
_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Get or create LLM client singleton"""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client


def reset_llm_client():
    """Reset client (use after config change)"""
    global _llm_client
    _llm_client = None
