from __future__ import annotations

import functools
import json
import re
from pathlib import Path

from langchain_core.tools import tool

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "reviews_raw.jsonl"

JUNK = {"", "。。。", "此用户未填写评价", "  "}
BRAND = {"airpods", "ipad", "iphone", "huawei", "xiaomi", "miui", "bose", "sony",
         "samsung", "apple", "hx3216", "tws", "ob", "app", "cpu", "usb", "led", "fm", "nfc"}


def load_reviews() -> list[dict]:
    """读全部原始评论。按商品过滤交给调用方，因为它们都还要按商品分组统计。"""
    with open(DATA, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


@tool
def clean_data(product_ids: list[str] | None = None, sample_limit: int = 8) -> dict:
    """清洗原始评论，剔除空文本、超短文本、纯表情、带链接、中英混排和完全重复的条目。

    在分析某个商品的评论之前先调用我；不确定有哪些商品时可以留空，我会清洗全部商品。
    我只返回统计数字和少量干净样本，不会把全部评论塞回对话。
    重复判断跨商品生效：同一条文字在两个商品下各出现一次，也只保留第一条。

    Args:
        product_ids: 商品 ID 列表，例如 ["TWS-X1", "TWS-P3"]；留空表示清洗全库。
        sample_limit: 最多返回几条干净评论样本，默认 8（在多个商品之间轮流取，避免全来自第一个商品）。
    """
    rows = load_reviews()
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        buckets.setdefault(r["product_id"], []).append(r)

    wanted = _wanted(product_ids)
    unknown = [p for p in wanted if p not in buckets]
    target = wanted or sorted(buckets)

    kept_by_sku: dict[str, list[dict]] = {}
    removed: dict[str, int] = {}
    seen: set[str] = set()

    def drop(kind: str) -> None:
        removed[kind] = removed.get(kind, 0) + 1

    for sku in target:
        for r in buckets.get(sku, []):
            text = (r.get("text") or "").strip()
            if text in JUNK or len(text) < 2:
                drop("empty"); continue
            if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", text):
                drop("pure_emoji"); continue
            if len(text) < 5:
                drop("too_short"); continue
            if "http" in text or "www." in text:
                drop("has_link"); continue
            if {w.lower() for w in re.findall(r"[A-Za-z]{4,}", text)} - BRAND:
                drop("mixed_lang"); continue
            if text in seen:
                drop("duplicate"); continue
            seen.add(text)
            kept_by_sku.setdefault(sku, []).append(
                {"review_id": r["review_id"], "product_id": sku,
                 "rating": int(r["rating"]), "text": text[:140]})

    pools = [kept_by_sku[s] for s in target if kept_by_sku.get(s)]
    samples: list[dict] = []
    for i in range(max((len(p) for p in pools), default=0)):
        for p in pools:
            if i < len(p) and len(samples) < sample_limit:
                samples.append(p[i])

    out = {
        "scope": target,
        "input": sum(len(buckets.get(s, [])) for s in target),
        "kept": sum(len(v) for v in kept_by_sku.values()),
        "removed": removed,
        "per_sku": {s: {"input": len(buckets.get(s, [])),
                        "kept": len(kept_by_sku.get(s, []))} for s in target},
        "samples": samples,
        "note": "removed 按第一条命中的规则归类，一条只计一次；样本在各商品之间轮流取",
    }
    if unknown:
        out["unknown_product_ids"] = unknown
        out["available_product_ids"] = sorted(buckets)
    return out


def _band(rating) -> str:
    """评分 → 口碑三档。int() 挡住真实数据里常见的 "4" / "4.0" 字符串评分。"""
    r = int(rating)
    return "pos" if r >= 4 else ("neu" if r == 3 else "neg")


def _wanted(product_ids: list[str] | None) -> list[str]:
    """把模型传来的商品 ID 清干净：跳过 None 和空串，去掉首尾空格。"""
    return [p.strip() for p in (product_ids or []) if p and p.strip()]


@tool
def sentiment_stats(product_ids: list[str] | None = None) -> dict:
    """按商品统计评论的评分分布与均值，用于竞品口碑对比。

    统计口径是评分分档（≥4 好评 / =3 中性 / ≤2 差评），不含任何文本情感判断——
    单条评论的情感由模型自己读文本判断，我不做这个判断。
    需要看具体评论原文时请配合 keyword_extract 或 clean_data 使用。

    Args:
        product_ids: 商品 ID 列表，例如 ["TWS-X1", "TWS-P3"]；留空表示统计全部商品。
    """
    rows = load_reviews()
    wanted = _wanted(product_ids)

    buckets: dict[str, list[dict]] = {}
    for r in rows:
        buckets.setdefault(r["product_id"], []).append(r)

    unknown = [p for p in wanted if p not in buckets]
    target = wanted or sorted(buckets)

    skus: dict[str, dict] = {}
    for sku in target:
        rs = buckets.get(sku, [])
        if not rs:
            continue
        hist = {str(k): sum(1 for r in rs if r["rating"] == k) for k in (5, 4, 3, 2, 1)}
        bands = {b: sum(1 for r in rs if _band(r["rating"]) == b) for b in ("pos", "neu", "neg")}
        skus[sku] = {
            "n": len(rs),
            "avg_rating": round(sum(r["rating"] for r in rs) / len(rs), 2),
            "rating_hist": hist,
            "bands": bands,
        }

    out: dict = {
        "scope": target,
        "skus": skus,
        "note": "分档基于用户评分，可能与评论文本情绪不一致（这类不一致本身就是洞察点）",
    }
    if unknown:
        out["unknown_product_ids"] = unknown
        out["available_product_ids"] = sorted(buckets)
    if not skus:
        out["error"] = "没有匹配到任何商品，请从 available_product_ids 里重选"
    return out


TOPICS_FILE = ROOT / "data" / "dimensions.json"


@functools.lru_cache(maxsize=1)
def load_topics() -> dict:
    with open(TOPICS_FILE, encoding="utf-8") as f:
        return json.load(f)


@tool
def keyword_extract(product_ids: list[str] | None = None,
                    top_n: int = 4, example_limit: int = 1) -> dict:
    """统计评论里提到了哪些话题（续航、降噪、客服、物流、外观做工、价格性价比、音质、清洁力）。

    每个话题返回：提到的评论条数、这些评论的平均分、一条例句原文。
    话题靠关键词词典命中，只能找到约 53% 的真实提及，所以条数是下限，
    不要说成"只有这么多人提到"。要看原文本身请用 clean_data。

    Args:
        product_ids: 商品 ID 列表，例如 ["TWS-M2", "TWS-X1"]；留空统计全部商品。
        top_n: 每个商品最多返回几个话题，默认 4 个（按条数从多到少）。
        example_limit: 每个话题最多给几条例句，默认 1 条。
    """
    rows = load_reviews()
    cfg = load_topics()
    topics = cfg["topics"]

    buckets: dict[str, list[dict]] = {}
    for r in rows:
        buckets.setdefault(r["product_id"], []).append(r)

    wanted = _wanted(product_ids)
    unknown = [p for p in wanted if p not in buckets]
    target = wanted or sorted(buckets)

    per_sku: dict[str, dict] = {}
    for sku in target:
        rs = buckets.get(sku, [])
        if not rs:
            continue
        found: list[dict] = []
        used: set[str] = set()
        for name, words in topics.items():
            hits = [r for r in rs if any(w in (r.get("text") or "") for w in words)]
            if not hits:
                continue
            # 例句优先给评分最低的（差评信息量最大），并避开其他话题已经用过的同一条评论
            ranked = sorted(hits, key=lambda r: (int(r["rating"]), r["review_id"]))
            picked = [r for r in ranked if r["review_id"] not in used][:example_limit]
            if not picked:
                picked = ranked[:example_limit]
            used.update(r["review_id"] for r in picked)
            found.append({
                "topic": name,
                "n": len(hits),
                "avg_rating": round(sum(int(r["rating"]) for r in hits) / len(hits), 2),
                "examples": [(r.get("text") or "").strip()[:60] for r in picked],
                "example_ratings": [int(r["rating"]) for r in picked],
            })
        found.sort(key=lambda x: -x["n"])
        per_sku[sku] = {
            "n_reviews": len(rs),
            "topics": found[:top_n],
            "topics_total": len(found),
        }

    out = {
        "scope": target,
        "per_sku": per_sku,
        "topic_coverage": cfg["eval"],
        "note": "n 是「提到该话题的评论条数」；一条评论可同时提到多个话题，"
                "所以各话题的 n 加起来会超过评论总数，不能当百分比用",
    }
    if unknown:
        out["unknown_product_ids"] = unknown
        out["available_product_ids"] = sorted(buckets)
    return out