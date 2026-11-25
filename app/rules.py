from __future__ import annotations

import re

JUNK = {"", "。。。", "此用户未填写评价", "  "}
BRAND = {"airpods", "ipad", "iphone", "huawei", "xiaomi", "miui", "bose", "sony",
         "samsung", "apple", "hx3216", "tws", "ob", "app", "cpu", "usb", "led", "fm", "nfc"}


def clean_text(row: dict) -> str:
    return (row.get("text") or "").strip()


def junk_reason(text: str) -> str | None:
    t = (text or "").strip()
    if t in JUNK or len(t) < 2:
        return "empty"
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", t):
        return "pure_emoji"
    if len(t) < 5:
        return "too_short"
    if "http" in t or "www." in t:
        return "has_link"
    if {w.lower() for w in re.findall(r"[A-Za-z]{4,}", t)} - BRAND:
        return "mixed_lang"
    return None


def dedupe(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """按文本去重。同一文本保留哪条不能靠文件顺序，否则 `-dup` 副本排在
    原件前面时，留下的就是副本、删掉的是干净原件（虽然文本一致不影响检索，
    但稳定 review_id 应当指向原件）。规则：优先保留非 `-dup` 的原件；
    都非 dup 或都 dup 时，保留首次出现的那条。"""
    chosen: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        t = clean_text(r)
        if t not in chosen:
            chosen[t] = r
            order.append(t)
        elif chosen[t].get("review_id", "").endswith("-dup") and not r.get("review_id", "").endswith("-dup"):
            chosen[t] = r  # 已选是副本，来的是原件 -> 换成原件
    kept = [chosen[t] for t in order]
    kept_ids = {id(r) for r in kept}
    dropped = [r for r in rows if id(r) not in kept_ids]
    return kept, dropped


def clean_rows(rows: list[dict]) -> tuple[list[dict], list[tuple[dict, str]]]:
    non_junk: list[dict] = []
    dropped: list[tuple[dict, str]] = []
    for r in rows:
        reason = junk_reason(clean_text(r))
        if reason:
            dropped.append((r, reason))
        else:
            non_junk.append(r)
    kept, dup_rows = dedupe(non_junk)
    dropped.extend((r, "duplicate") for r in dup_rows)
    return kept, dropped