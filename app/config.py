import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_deepseek import ChatDeepSeek

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

def get_llm(temperature: float = 0.2, thinking: bool = False):
    extra = {"thinking": {"type": "disabled"}} if not thinking else {}
    return ChatDeepSeek(
        model=os.getenv("LLM_MODEL"),
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL"),
        temperature=temperature,
        max_retries=1,
        timeout=90,
        extra_body=extra,
    )
