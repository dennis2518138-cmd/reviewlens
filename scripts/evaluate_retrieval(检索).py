"""D9 第①步：检索质量的『同源 vs 非同源』两栏评测。

口径与 scripts/check_retrieval.py 完全一致：
  - 运行时只走 app.rag.search；gold / dimensions 只在本离线脚本里读，绝不进运行时路径。
  - 相关 = expect_dim ∈ gold[review_id]["dimensions"]。

两个套件分开统计，禁止合并成一个总数：
  - 同源 SOURCE：查询词本身就是词表里的词（老 6 组，复现 README 的 6/6）。
  - 非同源 PARAPHRASE：口语/比喻/场景描述，且经 assert 保证不碰对应维度词表。

指标：hit@1 / hit@3 / MRR。MRR 取「第一条相关结果的名次」的倒数（第1=1.0,第2=0.5,第3≈0.33,top3 内无=0），再对全部查询求均值。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.rag import search  # noqa: E402  运行时通路：embedding + chroma

TOP_K = 3

# 同源套件：与 check_retrieval.py 的 CASES 逐字一致，用来复现老数字、校验判据没跑偏
SOURCE = [
    ("物流太慢", "物流"),
    ("续航不行", "续航"),
    ("降噪效果差", "降噪"),
    ("客服不回消息", "客服"),
    ("做工粗糙", "外观做工"),
    ("价格太贵", "价格性价比"),
]


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def overlap_word(query: str, dim: str, topics: dict) -> str | None:
    """查询里若出现该维度词表的任一词，或维度名本身，返回命中的那个词；否则 None。"""
    for w in list(topics.get(dim, [])) + [dim]:
        if w and w in query:
            return w
    return None


def eval_suite(name: str, cases: list[tuple[str, str]], gold: dict) -> dict:
    print(f"\n===== {name}（n={len(cases)}）=====")
    rows, hit1, hit3, rr_sum = [], 0, 0, 0.0
    for q, dim in cases:
        hits = search(q, TOP_K)
        flags = [dim in gold.get(h["id"], []) for h in hits]
        rank = next((i for i, f in enumerate(flags, 1) if f), 0)  # 第一条相关结果的名次
        rr = (1.0 / rank) if rank else 0.0
        hit1 += 1 if (flags and flags[0]) else 0
        hit3 += 1 if any(flags) else 0
        rr_sum += rr
        sims = [h["similarity"] for h in hits]
        print(f"  {q:<20s} 期望[{dim}] top3={''.join('✓' if f else '×' for f in flags)} "
              f"首个相关名次={rank} rr={rr:.3f} sim={sims}")
        rows.append({"query": q, "expect_dim": dim, "flags": flags,
                     "first_relevant_rank": rank, "rr": round(rr, 4),
                     "sims": sims, "top_ids": [h["id"] for h in hits]})
    n = len(cases)
    summary = {"suite": name, "n": n, "hit@1": hit1, "hit@3": hit3,
               "hit@1_rate": round(hit1 / n, 4), "hit@3_rate": round(hit3 / n, 4),
               "MRR": round(rr_sum / n, 4), "rows": rows}
    print(f"  >>> {name}: hit@1={hit1}/{n}({summary['hit@1_rate']}) "
          f"hit@3={hit3}/{n}({summary['hit@3_rate']}) MRR={summary['MRR']}")
    return summary


def main() -> None:
    topics = json.loads((ROOT / "data" / "dimensions.json").read_text(encoding="utf-8"))["topics"]
    gold = {g["review_id"]: g["dimensions"]
            for g in load_jsonl(ROOT / "data" / "reviews_gold.jsonl")}

    para = load_jsonl(ROOT / "data" / "queries_paraphrase.jsonl")
    cases = [(c["query"], c["expect_dim"]) for c in para]

    # 非同源断言：查询不许碰对应维度的词表词，也不许出现维度名本身。违规直接 raise，绝不"警告放行"。
    # 词表里没有的维度（如 连接稳定性 / 噪声）无词可校验，单独列出，不混进"已校验通过"。
    violations, uncheckable = [], []
    for c in para:
        q, dim = c["query"], c["expect_dim"]
        if dim not in topics:
            uncheckable.append((q, dim))
            continue
        w = overlap_word(q, dim, topics)
        if w:
            violations.append((q, dim, w))
    if violations:
        msg = "; ".join(f"「{q}」(维度{dim}) 命中词表词「{w}」" for q, dim, w in violations)
        raise SystemExit(f"非同源断言失败 —— 以下查询撞了词表，请改写：\n  {msg}")
    print(f"非同源断言：{len(cases) - len(uncheckable)} 条已过词表校验（0 撞车）；"
          f"{len(uncheckable)} 条维度无词表可校验 -> "
          + ", ".join(f"「{q}」[{dim}]" for q, dim in uncheckable))

    src = eval_suite("同源 SOURCE", SOURCE, gold)
    par = eval_suite("非同源 PARAPH", cases, gold)

    # ---- 稀有 token 召回（向量休克的场景，混合召回应当修好）----
    # 判据不是"相似度高"，而是"那条唯一含该 token 的评论有没有进 top3、排第几"。
    from app.rag import docs_containing, search_dense, search_hybrid
    rare_rows = []
    print("\n===== 稀有 token（dense vs hybrid）=====")
    for token in ("HX3216", "AirPods"):
        truth = set(docs_containing(token))
        d_hits = [h["id"] for h in search_dense(token, 3)]
        h_hits = [h["id"] for h in search_hybrid(token, 3)]
        d_rank = next((i for i, x in enumerate(d_hits, 1) if x in truth), 0)
        h_rank = next((i for i, x in enumerate(h_hits, 1) if x in truth), 0)
        print(f"  {token:<10} 库内命中{len(truth)}条  dense名次={d_rank or '未命中'}  "
              f"hybrid名次={h_rank or '未命中'}")
        rare_rows.append({"token": token, "in_corpus": len(truth),
                          "dense_rank": d_rank, "hybrid_rank": h_rank})

    print(f"\n===== 两栏对照（禁止合并）=====")
    print(f"  同源   hit@1={src['hit@1_rate']}  hit@3={src['hit@3_rate']}  MRR={src['MRR']}")
    print(f"  非同源 hit@1={par['hit@1_rate']}  hit@3={par['hit@3_rate']}  MRR={par['MRR']}")

    out = {"generated": "D11-e1", "top_k": TOP_K, "source_suite": src,
           "paraphrase_suite": par, "rare_token_suite": rare_rows,
           "retrieval_mode": __import__("os").getenv("RETRIEVAL_MODE", "hybrid"),
           "uncheckable_dims": [{"query": q, "expect_dim": d} for q, d in uncheckable]}
    (ROOT / "data" / "eval_retrieval.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n落盘 -> data/eval_retrieval.json")


if __name__ == "__main__":
    main()
