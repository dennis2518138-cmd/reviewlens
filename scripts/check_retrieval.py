import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.rag import search                       # noqa: E402

# 查询 -> 期望维度。gold 只在这个离线脚本里读，运行时检索路径绝不碰它（否则评测作弊）
CASES = [("物流太慢", "物流"), ("续航不行", "续航"), ("降噪效果差", "降噪"),
         ("客服不回消息", "客服"), ("做工粗糙", "外观做工"), ("价格太贵", "价格性价比")]


def main() -> None:
    gold_path = ROOT / "data" / "reviews_gold.jsonl"
    gold = {g["review_id"]: g["dimensions"] for g in
            (json.loads(l) for l in gold_path.read_text(encoding="utf-8").splitlines() if l.strip())}

    hit3 = hit1 = 0
    for q, dim in CASES:
        top = search(q, 3)
        flags = [dim in gold.get(h["id"], []) for h in top]
        hit3 += any(flags)
        hit1 += flags[0] if flags else 0
        sims = [round(h["similarity"], 2) for h in top]
        print(f"  {q:<8s} 期望[{dim}]  top3={''.join('✓' if f else '×' for f in flags)}  sim={sims}")

    n = len(CASES)
    print(f"\n  hit@1 = {hit1}/{n}   hit@3 = {hit3}/{n}")

    # 精确字符串实验：库里只有 1 条文本含 HX3216，看纯向量能不能捞到它
    print("\n  —— 稀有 token 实验 ——")
    for h in search("HX3216", 3):
        print(f"    sim={h['similarity']:.2f} {h['product_id']} | {h['text'][:26]}")


if __name__ == "__main__":
    main()
