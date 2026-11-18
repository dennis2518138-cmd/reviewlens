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
    """按文本去重，保留第一条。返回 (留下的行, 被去掉的行)。

    返回「行」而不是数量：调用方要按商品范围分类统计剔除数，
    只给一个数字的话每个工具得自己再算一遍，那就是另一个会漂移的口径。
    """
    seen: set[str] = set()
    kept: list[dict] = []
    dropped: list[dict] = []
    for r in rows:
        t = clean_text(r)
        if t in seen:
            dropped.append(r)
            continue
        seen.add(t)
        kept.append(r)
    return kept, dropped


def clean_rows(rows: list[dict]) -> tuple[list[dict], list[tuple[dict, str]]]:
    """唯一的清洗流水线，所有工具都走它，口径才不可能漂移。

    顺序是设计：先过格式规则，再去重。因为「一条空文本的副本」真正的问题是它空，
    不是它重复；顺序反过来，剔除分布会变，README 里记录的数字就得全部重测。
    """
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