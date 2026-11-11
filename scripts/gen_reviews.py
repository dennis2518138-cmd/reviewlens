"""D1 数据资产：生成 ReviewLens 用的模拟电商评论（带金标注 + 人为噪声）。

产出（都在 data/ 下）：
  reviews_raw.jsonl   原始数据，故意含脏（重复/空文本/纯表情/评分与文本矛盾/中英混/超短）
  reviews_gold.jsonl  同一条数据的金标注（真实情感/维度/是否脏），用于日后评估清洗与情感分析
  skus.json           商品目录（2 个品类 × 2-3 个品牌，供竞品对比子图使用）

用法: python scripts/gen_reviews.py [总条数，默认200]
"""
from __future__ import annotations
import json, os, random, re, sys, time
from pathlib import Path

from dotenv import load_dotenv
from langchain_deepseek import ChatDeepSeek
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
load_dotenv(ROOT / ".env")

CATALOG = [
    {"product_id": "TWS-X1", "brand": "声浪", "category": "真无线蓝牙耳机", "sku": ["黑", "白", "薄荷绿"], "price": 249},
    {"product_id": "TWS-P3", "brand": "听泉", "category": "真无线蓝牙耳机", "sku": ["黑", "白"], "price": 299},
    {"product_id": "TWS-M2", "brand": "声浪", "category": "真无线蓝牙耳机", "sku": ["灰", "粉"], "price": 159},
    {"product_id": "OB-A6", "brand": "皓齿", "category": "声波电动牙刷", "sku": ["象牙白", "雾蓝"], "price": 199},
    {"product_id": "OB-B2", "brand": "洁齿乐", "category": "声波电动牙刷", "sku": ["白", "粉", "黑"], "price": 169},
    {"product_id": "OB-C9", "brand": "皓齿", "category": "声波电动牙刷", "sku": ["象牙白"], "price": 349},
]

DIMENSIONS = ["音质", "续航", "佩戴舒适度", "外观做工", "物流", "客服", "价格性价比",
              "降噪", "连接稳定性", "清洁力", "刷头耐用度", "噪声"]


class Review(BaseModel):
    rating: int = Field(description="1-5 星")
    text: str = Field(description="中文评论正文，20-120 字，口语化，像真实买家")
    sentiment: str = Field(description="gold 标注：pos / neu / neg")
    dimensions: list[str] = Field(description="评论涉及的维度，从给定维度表里选 0-3 个")
    helpful_votes: int = Field(description="有用数，0-30")


class Batch(BaseModel):
    reviews: list[Review]


SYSTEM = (
    "你是电商评论数据模拟器。生成的数据用于 Agent 训练与评测，需要覆盖正/中/负三种情感，"
    "并且要在真实维度上有差异：有人夸音质骂续航，有人只谈物流，有人情绪化无信息。"
    "评论要有具体细节（型号、场景、对比），不要每条都夸同一件事，不要出现编号或列表格式。"
    f"可用维度表：{DIMENSIONS}"
)


def llm() -> ChatDeepSeek:
    return ChatDeepSeek(
        model=os.environ.get("LLM_MODEL", "deepseek-ai/DeepSeek-V4.1-Flash-1017"),
        api_key=os.environ["LLM_API_KEY"],
        base_url=os.environ["LLM_BASE_URL"],
        temperature=0.9,
        max_retries=2,
        timeout=90,
    ).with_structured_output(Batch)


def gen(product: dict, n: int, chain, rnd: random.Random) -> list[dict]:
    sku = rnd.choice(product["sku"])
    prompt = (f"商品：{product['brand']} {product['category']}（型号 {product['product_id']}，售价 {product['price']} 元）"
              f"，本次买家选的颜色：{sku}。请生成 {n} 条买家评论，情感分布大致 正向55% 中性20% 负向25%。"
              f"至少要有一条只抱怨同一件事的、一条给出具体对比的。")
    out = []
    for attempt in range(3):
        try:
            batch = chain.invoke([{"role": "system", "content": SYSTEM},
                                  {"role": "user", "content": prompt}])
            for i, r in enumerate(batch.reviews):
                out.append({
                    "review_id": f"{product['product_id']}-{int(time.time())%100000}-{attempt}{i}",
                    "product_id": product["product_id"],
                    "brand": product["brand"],
                    "category": product["category"],
                    "sku": sku,
                    "rating": max(1, min(5, r.rating)),
                    "text": r.text.strip(),
                    "date": f"2026-{rnd.randint(1,10):02d}-{rnd.randint(1,28):02d}",
                    "user_id": f"u{rnd.randint(100000,999999)}",
                    "helpful_votes": max(0, r.helpful_votes),
                    "has_image": rnd.random() < 0.35,
                    "gold_sentiment": r.sentiment,
                    "gold_dimensions": r.dimensions,
                    "is_dirty": False,
                })
            return out
        except Exception as e:
            print(f"  retry {attempt+1}: {type(e).__name__} {str(e)[:90]}")
            time.sleep(2)
    return out


def inject_noise(rows: list[dict], rnd: random.Random) -> list[dict]:
    """人为加脏：清洗工具（D4）必须有真东西可洗。"""
    noisy = []
    n = len(rows)
    dup_idx = rnd.sample(range(n), max(1, n // 20))
    for i in dup_idx:
        d = dict(rows[i]); d["review_id"] += "-dup"; d["is_dirty"] = True
        noisy.append(d)
    empty_idx = rnd.sample(range(n), max(1, n // 40))
    for i in empty_idx:
        rows[i] = dict(rows[i]); rows[i]["text"] = rnd.choice(["", "  ", "。。。", "此用户未填写评价"])
        rows[i]["is_dirty"] = True
    emoji_idx = rnd.sample(range(n), max(1, n // 40))
    for i in emoji_idx:
        rows[i] = dict(rows[i]); rows[i]["text"] = rnd.choice(["👍👍👍", "好", "还行吧", "？", "哈哈哈哈"])
        rows[i]["is_dirty"] = True
    mix_idx = rnd.sample(range(n), max(1, n // 50))
    for i in mix_idx:
        rows[i] = dict(rows[i])
        rows[i]["text"] = rows[i]["text"] + rnd.choice([" btw 客服 response 很慢", " 详情见 https://t.cn/abc123 有图",
                                                        " 五颗星!!! 五星好评!!!"])
        rows[i]["is_dirty"] = True
    conflict_idx = rnd.sample(range(n), max(1, n // 25))
    for i in conflict_idx:
        rows[i] = dict(rows[i]); rows[i]["rating"] = 6 - rows[i]["rating"]
        rows[i]["is_dirty"] = True
    return rows + noisy


def main() -> None:
    total = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    rnd = random.Random(20261111)
    chain = llm()
    per = max(4, total // len(CATALOG))
    rows: list[dict] = []
    for idx, p in enumerate(CATALOG):
        want = per if idx < len(CATALOG) - 1 else total - len(rows)
        got = gen(p, want, chain, rnd)
        print(f"{p['product_id']:8s} {p['brand']}{p['category']}: {len(got)}/{want}")
        rows.extend(got)
    rows = inject_noise(rows, rnd)
    rnd.shuffle(rows)

    with open(DATA / "reviews_raw.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(DATA / "reviews_gold.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"review_id": r["review_id"], "product_id": r["product_id"],
                                "sentiment": r["gold_sentiment"], "dimensions": r["gold_dimensions"],
                                "is_dirty": r["is_dirty"]}, ensure_ascii=False) + "\n")
    (DATA / "skus.json").write_text(json.dumps(CATALOG, ensure_ascii=False, indent=2), encoding="utf-8")

    texts = [r["text"] for r in rows]
    pos = sum(1 for r in rows if r["gold_sentiment"] == "pos")
    print(json.dumps({
        "rows": len(rows), "unique_text": len(set(texts)),
        "dirty": sum(1 for r in rows if r["is_dirty"]),
        "sentiment": {"pos": pos, "neu": sum(1 for r in rows if r["gold_sentiment"] == "neu"),
                      "neg": sum(1 for r in rows if r["gold_sentiment"] == "neg")},
        "avg_len": round(sum(len(t) for t in texts) / len(texts), 1),
        "files": [str(DATA / "reviews_raw.jsonl"), str(DATA / "reviews_gold.jsonl"), str(DATA / "skus.json")],
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
