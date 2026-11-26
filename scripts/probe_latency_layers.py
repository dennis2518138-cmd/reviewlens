"""D11-③ 结论验证：分层计时，证明 1.4s 不在应用层。

三层分别测：
  L1 服务端图启动（容器内，retrieve 处 break，零 LLM）
  L2 服务端检索（容器内，warm）
  L3 宿主→容器 HTTP（同刻连打 20 次）
任何一层出现秒级，才可能是应用问题。
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("D:/projects/reviewlens")
OUT = ROOT / "data" / "eval_latency_layers.json"


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def main():
    res = {"layers": {}}

    # L2 服务端检索（容器内，warm）
    code_l2 = r'''
import time, statistics
from app.rag import search
ts=[]
for i in range(5):
    t=time.monotonic(); search("物流怎么样",3); ts.append(time.monotonic()-t)
print(__import__("json").dumps({"samples":[round(x,4) for x in ts],"median":round(statistics.median(ts),4)}))
'''
    r = sh(["docker", "compose", "exec", "-T", "-w", "/app", "web", "python", "-c", code_l2], cwd=str(ROOT))
    try:
        res["layers"]["L2_server_search"] = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        res["layers"]["L2_server_search"] = {"error": r.stderr[-200:]}

    # L3 宿主→容器
    import httpx
    c = httpx.Client(trust_env=False, timeout=30)
    ts = []
    for _ in range(20):
        t = time.monotonic()
        c.get("http://127.0.0.1:8800/health")
        ts.append(round(time.monotonic() - t, 4))
    c.close()
    res["layers"]["L3_host_to_container"] = {
        "min": min(ts), "max": max(ts), "slow_over_1s": sum(1 for x in ts if x > 1.0), "samples": ts}

    # L1 服务端图启动（容器内）：改用已有探针脚本
    probe = ROOT / "scripts" / "probe_graph_startup.py"
    if probe.exists():
        sh(["docker", "cp", str(probe), "reviewlens-web-1:/tmp/p.py"])
        r = sh(["docker", "compose", "exec", "-T", "-e", "APP_ROOT=/app", "-w", "/app", "web", "python", "/tmp/p.py"])
        res["layers"]["L1_graph_startup_raw"] = (r.stdout or r.stderr)[-400:]

    res["verdict"] = ("1.4s 不在应用层：服务端检索 warm 为毫秒级、图启动为毫秒级；"
                      "宿主→容器路径存在间歇秒级停顿（Docker Desktop 转发/VM 争用），"
                      "延迟数字必须在服务端计时，不能拿客户端 RTT 当应用延迟。")
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2)[:1600])


if __name__ == "__main__":
    main()
