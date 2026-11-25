"""D9 第②步：脏数据检测评测（零 LLM，210 条全量）。

既定口径（README / 记忆铁律，禁止合并成单一召回率）：脏样本分两栏报——
  A 格式类 = 规则可达（junk_reason 的 5 类），按 gold is_dirty 算 recall，并查清洗是否误杀干净样本；
  B 重复类 = dedupe，按【文本分组】计分（不能按 review_id 对齐 gold，否则删原件会误报假阳性）；
  C 语义矛盾类 = 评分与文本情感相反，规则【本就不负责】，单独报 X/N，绝不并进 A 的召回率。

运行时只读离线数据，绝不碰 gold 以外的标注；gold 只在本脚本读。
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.rules import junk_reason, clean_text, clean_rows  # noqa: E402

RAW = ROOT / "data" / "reviews_raw.jsonl"
GOLD = ROOT / "data" / "reviews_gold.jsonl"


def load(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> None:
    raw_rows = load(RAW)
    raw = {r["review_id"]: r for r in raw_rows}
    gold = {g["review_id"]: g for g in load(GOLD)}

    # 分类 gold 脏样本（用规则可达性 + -dup 标记，与运行时 clean_rows 同口径）
    dirty = [rid for rid, g in gold.items() if g.get("is_dirty")]
    fmt_dirty = [rid for rid in dirty if junk_reason(clean_text(raw.get(rid, {})))]
    dup_dirty = [rid for rid in dirty if rid.endswith("-dup") and rid not in fmt_dirty]
    sem_dirty = [rid for rid in dirty if rid not in fmt_dirty and rid not in dup_dirty]
    print(f"gold 脏样本 {len(dirty)}：格式类 {len(fmt_dirty)} / 重复类 {len(dup_dirty)} / 语义矛盾 {len(sem_dirty)}")

    # 运行真实清洗通路
    kept, dropped = clean_rows(raw_rows)
    dropped_ids = {r["review_id"]: reason for r, reason in dropped}

    # ===== A 格式类 =====
    a_caught = [rid for rid in fmt_dirty if rid in dropped_ids]
    a_recall = len(a_caught) / len(fmt_dirty) if fmt_dirty else 0.0
    # 误杀：gold 干净却被规则判脏（仅算 junk 类理由，dup 在 B 单独评）
    clean_rows_flagged_junk = [rid for rid, rsn in dropped_ids.items()
                               if rsn != "duplicate" and not gold.get(rid, {}).get("is_dirty")]
    print(f"\n[A 格式类] recall = {len(a_caught)}/{len(fmt_dirty)} = {a_recall:.3f}")
    print(f"          误杀干净样本(格式理由) = {len(clean_rows_flagged_junk)} {clean_rows_flagged_junk}")

    # ===== B 重复类（按文本分组） =====
    groups = defaultdict(list)
    for r in raw_rows:
        groups[clean_text(r)].append(r["review_id"])
    dup_groups = {t: ids for t, ids in groups.items() if len(ids) > 1 and t}
    collapsed = sum(1 for ids in dup_groups.values() if len(set(ids) - set(kept_ids(kept))) >= 1)
    # kept 里每个重复组是否只剩 1 条
    leftover = {t: [i for i in ids if i in {k["review_id"] for k in kept}]
                for t, ids in dup_groups.items()}
    kept_per_group = Counter(len(v) for v in leftover.values())
    print(f"\n[B 重复类] 文本重复组 = {len(dup_groups)} 组")
    print(f"          清洗后每组剩余条数分布 = {dict(kept_per_group)}  (期望每组=1)")
    over = {t: ids for t, ids in leftover.items() if len(ids) > 1}
    under = {t: ids for t, ids in leftover.items() if len(ids) == 0}
    print(f"          未塌缩(组内>1) = {len(over)}；整组被误删空(组内=0) = {len(under)}")

    # ===== C 语义矛盾类（规则不负责，单独报） =====
    a_caught_sem = [rid for rid in sem_dirty if rid in dropped_ids]
    print(f"\n[C 语义矛盾] 规则可达 recall = {len(a_caught_sem)}/{len(sem_dirty)} = "
          f"{len(a_caught_sem)/len(sem_dirty) if sem_dirty else 0:.3f}  ← 设计如此，交给 sentiment/compare(D5)")
    for rid in sem_dirty:
        r, g = raw.get(rid, {}), gold.get(rid, {})
        print(f"          [{rid}] rating={r.get('rating')} 情感标={g.get('sentiment')} 文本={ (r.get('text') or '')[:34] }")

    # ===== 汇总（两栏，不合并） =====
    out = {
        "suite": "D9-e2",
        "total": len(raw_rows),
        "dirty_total": len(dirty),
        "format_class": {"n": len(fmt_dirty), "caught": len(a_caught), "recall": round(a_recall, 4),
                         "false_kill_clean": len(clean_rows_flagged_junk)},
        "dup_class": {"text_groups": len(dup_groups), "group_size_after_kept": dict(kept_per_group)},
        "semantic_class": {"n": len(sem_dirty), "rule_caught": len(a_caught_sem),
                           "note": "规则不负责，单独报，不并入格式召回"},
    }
    (ROOT / "data" / "eval_dirty.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n落盘 -> data/eval_dirty.json")


def kept_ids(kept):
    return {r["review_id"] for r in kept}


if __name__ == "__main__":
    main()
