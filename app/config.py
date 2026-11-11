"""配置与 LLM 工厂。端点/模型已实测可用，别改成官方 api.deepseek.com（本机没有官方 key）。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.tokenjuice.ai/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-ai/DeepSeek-V4.1-Flash-1017")
SQLITE_PATH = ROOT / os.getenv("SQLITE_PATH", "./data/reviewlens.db")
CHROMA_PATH = ROOT / os.getenv("CHROMA_PATH", "./data/chroma")
MODEL_DIR = ROOT / "models" / "bge-small-zh-v1.5"


def get_llm(temperature: float = 0.2, **kwargs):
    """返回 ChatDeepSeek（OpenAI 兼容）。支持 bind_tools / with_structured_output，已实测。"""
    from langchain_deepseek import ChatDeepSeek

    key = os.getenv("LLM_API_KEY")
    if not key:
        raise RuntimeError("LLM_API_KEY 未设置，检查 .env")
    return ChatDeepSeek(
        model=LLM_MODEL,
        api_key=key,
        base_url=LLM_BASE_URL,
        temperature=temperature,
        max_retries=1,      # 这个中转重试会放大 429，宁可快速失败
        timeout=90,
        **kwargs,
    )


@lru_cache(maxsize=1)
def get_embedder():
    """本地中文 embedding（ONNX，无需 torch）。D3 用。"""
    from scripts.embed_ref import BgeSmallZh

    return BgeSmallZh(MODEL_DIR)
