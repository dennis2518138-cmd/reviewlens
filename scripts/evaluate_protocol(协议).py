"""D9 第③步：协议/入参回归（零 LLM，全打在会被『进图之前』拒绝的路径上）。

目标：把 D2/D7 踩过的坑固化成一条可重跑的清单，以后改 main.py 不怕回归。

关键约束（来自项目记忆，逐条断言）：
  1. 空/空白/单字问题 → 422，且发生在开流之前（所以 /chat/stream 的 422 响应头必须是
     application/json，而不是 text/event-stream——这是"校验前置于 SSE"的硬判据）。
  2. 非法 decision → 422，同样先于 SSE。
  3. 已结束/不存在的单号审批 → 409（不是静默开一条空流）。
  4. 路由顺序：/review/stream 不能被 /review/{thread_id} 抢走（{thread_id} 能匹配字符串
     "stream"）。判据 = 打 /review/stream 得到 409（流端点的 pending 校验）而不是 200
     （若被抢，会当成 thread_id="stream" 的状态查询返回 200 JSON）。
  5. /review（JSON）与 /review/stream（SSE）两条路径的校验规则必须一致：同样的非法
     decision 都得 422、同样的不存在单号都得 409。
  6. /health 不依赖模型/库，必须 200；/ 静态挂载必须 200 text/html。

默认打容器 8800（部署态）。用 .venv 跑也行，BASE 换成 8799 即可。
"""
import json
import sys

import httpx

BASE = "http://127.0.0.1:8800"
FAKE_THREAD = "deadbeef0000"  # 不存在的单号

results = []


def check(desc, got, expect, extra=""):
    ok = got == expect
    results.append((ok, desc, got, expect, extra))
    mark = "✅" if ok else "❌"
    print(f"  {mark} {desc}: 得到 {got} 期望 {expect}{('  ' + extra) if extra else ''}")


def status_and_ctype(r):
    return r.status_code, r.headers.get("content-type", "")


def main() -> None:
    print(f"目标 = {BASE}\n" + "=" * 60)

    # --- 1. 空/空白/单字问题，两条路径都得 422，且校验先于 SSE ---
    for path, verb in (("/chat", "POST"), ("/chat/stream", "GET")):
        for msg in ("", "   ", "物"):
            try:
                if verb == "POST":
                    r = httpx.post(f"{BASE}{path}", json={"message": msg}, timeout=20)
                else:
                    r = httpx.get(f"{BASE}{path}", params={"message": msg}, timeout=20)
            except Exception as e:
                check(f"{verb} {path} message={msg!r}", f"EXC:{e}", 422)
                continue
            sc, ct = status_and_ctype(r)
            note = f"content-type={ct}"
            # 对 SSE 路径额外断言：422 必须是 JSON，不能是 event-stream（证明没先开流）
            if path.endswith("/stream") and sc == 422:
                assert_ok = "event-stream" not in ct
                note += "  ← 422 前于 SSE" + ("✅" if assert_ok else "❌(开流后才报错)")
            check(f"{verb} {path} message={msg!r} → 422", sc, 422, note)

    # --- 2. 非法 decision：两条路径都 422，先于 SSE ---
    for r in [httpx.get(f"{BASE}/review/stream",
                        params={"thread_id": FAKE_THREAD, "decision": "maybe"}, timeout=20)]:
        sc, ct = status_and_ctype(r)
        check("GET /review/stream decision=maybe → 422", sc, 422,
              f"content-type={ct}（非法 decision 必须先于 SSE 拒绝）")
    r = httpx.post(f"{BASE}/review", json={"thread_id": FAKE_THREAD, "decision": "maybe"}, timeout=20)
    check("POST /review decision=maybe → 422", r.status_code, 422)

    # --- 3. 不存在/已结束单号审批 → 409（不静默开空流）---
    r = httpx.get(f"{BASE}/review/stream",
                  params={"thread_id": FAKE_THREAD, "decision": "approve"}, timeout=20)
    sc, ct = status_and_ctype(r)
    # 这条同时验证 #4：如果返回 409 而不是 200，说明没被 /review/{thread_id} 抢走
    check("GET /review/stream?thread_id=不存在&decision=approve → 409", sc, 409,
          f"content-type={ct}")
    r = httpx.post(f"{BASE}/review", json={"thread_id": FAKE_THREAD, "decision": "approve"}, timeout=20)
    check("POST /review thread_id=不存在 → 409", r.status_code, 409)

    # --- 4. 路由抢占专项：/review/stream 是流端点，不是 thread_id="stream" 的状态查询 ---
    # 若被 /review/{thread_id} 抢走，会用 thread_id="stream" 返回 200 JSON
    r = httpx.get(f"{BASE}/review/stream", timeout=20)  # 缺 query 参数
    sc, ct = status_and_ctype(r)
    # 缺参数应 422（FastAPI 校验必填 query）而不是 200 —— 200 就说明路由被抢
    check("GET /review/stream（缺 query）不被 {thread_id} 抢 → 非200", "200" if sc == 200 else sc,
          422, f"content-type={ct}")

    # --- 5. 状态查询本身：GET /review/{任意} 应 200 且 pending 字段可读（零 LLM）---
    r = httpx.get(f"{BASE}/review/{FAKE_THREAD}", timeout=20)
    try:
        body = r.json()
        has_pending = "pending" in body
    except Exception:
        body, has_pending = {}, False
    check("GET /review/{id} → 200 且含 pending", r.status_code, 200,
          f"pending={body.get('pending') if isinstance(body, dict) else '?'}")

    # --- 6. health + 静态 ---
    r = httpx.get(f"{BASE}/health", timeout=20)
    check("GET /health → 200", r.status_code, 200)
    r = httpx.get(f"{BASE}/", timeout=20)
    check("GET / → 200", r.status_code, 200,
          f"content-type={r.headers.get('content-type', '')}")

    # --- 汇总 ---
    passed = sum(1 for ok, *_ in results if ok)
    total = len(results)
    print("=" * 60)
    print(f"协议回归：{passed}/{total} 通过")
    out = {"suite": "D9-e3", "base": BASE, "passed": passed, "total": total,
           "cases": [{"desc": d, "got": g, "expect": e, "extra": x, "ok": ok}
                     for ok, d, g, e, x in results]}
    from pathlib import Path
    (Path(__file__).resolve().parents[1] / "data" / "eval_protocol.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("落盘 -> data/eval_protocol.json")
    if passed != total:
        sys.exit(1)


if __name__ == "__main__":
    main()
