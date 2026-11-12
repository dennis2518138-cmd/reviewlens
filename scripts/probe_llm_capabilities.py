"""能力探针：一个 OpenAI 兼容端点(tool calling / structured output)到底能不能支撑 Agent 项目。

用法:
    python probe_llm_capabilities.py <base_url> <KEY_ENV_NAME> <model>
例:
    python probe_llm_capabilities.py https://api.tokenjuice.ai/v1 TOKENJUICE_API_KEY deepseek-ai/DeepSeek-V4.1-Flash-1017

三项全绿才敢对方案点头：
    chat       普通对话
    tool_call  bind_tools 的 function calling（ToolNode 依赖）
    structured with_structured_output（编排器路由依赖）

要按 (base_url, model) 组合跑：同一个模型在不同中转上的能力可以完全不同。
"""
import json
import os
import sys
import time

base_url, key_env, model = sys.argv[1], sys.argv[2], sys.argv[3]
key = os.environ.get(key_env)
if not key:
    print("SKIP: env", key_env, "unset")
    sys.exit(2)

from langchain_deepseek import ChatDeepSeek
from pydantic import BaseModel, Field

kw = dict(model=model, api_key=key, temperature=0, max_retries=1, timeout=90)
try:
    llm = ChatDeepSeek(base_url=base_url, **kw)
except TypeError:                 # 旧版字段名
    llm = ChatDeepSeek(api_base=base_url, **kw)

res = {}

t0 = time.time()
try:
    r = llm.invoke("用一句中文回答：评论情感分析的核心指标是什么？")
    res["chat"] = f"OK {time.time()-t0:.1f}s :: {str(r.content)[:60]}"
except Exception as e:
    res["chat"] = f"FAIL {type(e).__name__}: {str(e)[:160]}"

try:
    t0 = time.time()
    tool_llm = llm.bind_tools([{
        "type": "function",
        "function": {"name": "sentiment_stats",
                     "description": "统计一批评论的情感分布",
                     "parameters": {"type": "object",
                                    "properties": {"text": {"type": "string", "description": "评论原文"}},
                                    "required": ["text"]}},
    }])
    r = tool_llm.invoke("请调用 sentiment_stats 分析这句评论：'质量很好，但是物流太慢'")
    tc = getattr(r, "tool_calls", []) or []
    res["tool_call"] = (f"OK {time.time()-t0:.1f}s :: {tc[0]['name']}({tc[0]['args']})" if tc
                        else f"NO_TOOL_CALL :: {str(r.content)[:80]}")
except Exception as e:
    res["tool_call"] = f"FAIL {type(e).__name__}: {str(e)[:160]}"


class Plan(BaseModel):
    tasks: list[str] = Field(description="任务分解列表")
    needs_rag: bool = Field(description="是否需要检索历史数据")


try:
    t0 = time.time()
    r = llm.with_structured_output(Plan).invoke("任务：分析某商品最近200条评论并出报告。拆成3步。")
    res["structured"] = f"OK {time.time()-t0:.1f}s :: {r}"
except Exception as e:
    # DeepSeek 官方 deepseek-flash 默认思考模式，思考模式不支持强制 tool_choice。
    # 实测 2026-11-11：extra_body={"thinking": {"type": "disabled"}} 后正常。回退一次再判失败。
    if "tool_choice" in str(e):
        try:
            t0 = time.time()
            # extra_body 是 ChatDeepSeek 的构造参数，.bind() 传不进去，必须重建实例
            fb = ChatDeepSeek(model=model, api_key=key, base_url=base_url,
                              temperature=0, max_retries=1, timeout=60,
                              extra_body={"thinking": {"type": "disabled"}})
            r = fb.with_structured_output(Plan).invoke("任务：分析某商品最近200条评论并出报告。拆成3步。")
            res["structured"] = f"OK(关思考回退) {time.time()-t0:.1f}s :: {r}"
        except Exception as e2:
            res["structured"] = f"FAIL {type(e2).__name__}: {str(e2)[:160]}"
    else:
        res["structured"] = f"FAIL {type(e).__name__}: {str(e)[:160]}"

print(json.dumps({"endpoint": base_url, "model": model, **res}, ensure_ascii=False, indent=1))
