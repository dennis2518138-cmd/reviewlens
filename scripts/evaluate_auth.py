"""鉴权验收（零 LLM）：对着一个设了 API_TOKEN 的实例，逐条验 401/放行。

期望：
  - /health、/（静态）        → 不带 token 也要 200（健康检查与页面本身不含数据）
  - /chat、/chat/stream       → 无 token 401；带 ?token= 或 Bearer 头后分别落到 422（空问题）
  - /review/{id}              → 无 token 401；带 token 后 200（不存在单号返回 pending=false）
  - /review/stream            → 无 token 401；带 token 后对不存在单号 409
  - 错误 token                → 401
"""
import os
import sys

import httpx

BASE = os.environ.get("BASE", "http://127.0.0.1:8801")
TOKEN = os.environ.get("API_TOKEN", "test123")
FAKE = "deadbeef0000"
results = []


def check(desc, got, expect):
    ok = got == expect
    results.append(ok)
    print(f"  {'✅' if ok else '❌'} {desc}: {got} (期望 {expect})")


def main():
    c = httpx.Client(base_url=BASE, timeout=20, trust_env=False)

    # 公开端点
    check("GET /health 无 token", c.get("/health").status_code, 200)
    check("GET / 无 token", c.get("/").status_code, 200)

    # 无 token → 401
    check("POST /chat 无 token", c.post("/chat", json={"message": "物流怎么样"}).status_code, 401)
    check("GET /chat/stream 无 token", c.get("/chat/stream", params={"message": "物流怎么样"}).status_code, 401)
    check("GET /review/{id} 无 token", c.get(f"/review/{FAKE}").status_code, 401)
    check("GET /review/stream 无 token", c.get("/review/stream",
          params={"thread_id": FAKE, "decision": "approve"}).status_code, 401)

    # 错误 token
    check("GET /chat/stream 错误 token", c.get("/chat/stream",
          params={"message": "物流怎么样", "token": "wrong"}).status_code, 401)

    # 正确 token → 落到各自的业务校验（不是 401/403）
    check("GET /chat/stream 带 token 且空问题", c.get("/chat/stream",
          params={"message": "", "token": TOKEN}).status_code, 422)
    check("GET /review/{id} 带 token", c.get(f"/review/{FAKE}", params={"token": TOKEN}).status_code, 200)
    check("GET /review/stream 带 token 且不存在单号", c.get("/review/stream",
          params={"thread_id": FAKE, "decision": "approve", "token": TOKEN}).status_code, 409)
    check("POST /chat 带 Bearer 头", c.post("/chat", json={"message": "物"},
          headers={"Authorization": f"Bearer {TOKEN}"}).status_code, 422)

    passed, total = sum(results), len(results)
    print(f"\n鉴权验收：{passed}/{total}")
    c.close()
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
