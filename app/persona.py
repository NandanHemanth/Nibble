"""The shopper's persona: stated preferences plus habits learned from receipts.

Stored locally in .nibble/persona.json. It steers which listings count as a
match (organic only, store brands allowed, stores to skip), so the cheapest
basket is the cheapest basket this shopper would actually buy.
"""
from __future__ import annotations

import copy
import json
import time

from . import config

PATH = config.DATA_DIR / "persona.json"

DEFAULT = {
    "organic_only": False,
    "store_brands_ok": True,
    "diet": "",
    "skip_stores": [],
    "learned": [],
    "history": [],
}


def load() -> dict:
    fresh = copy.deepcopy(DEFAULT)  # never hand out the shared default lists
    try:
        return {**fresh, **json.loads(PATH.read_text())}
    except Exception:
        return fresh


def save(persona: dict) -> dict:
    clean = {**DEFAULT, **{k: v for k, v in persona.items() if k in DEFAULT}}
    clean["diet"] = str(clean["diet"])[:80]
    clean["skip_stores"] = [str(s)[:40] for s in clean["skip_stores"]][:10]
    clean["learned"] = [str(s)[:40] for s in clean["learned"]][:12]
    clean["history"] = clean["history"][-30:]
    PATH.write_text(json.dumps(clean, indent=2))
    return clean


def update(patch: dict) -> dict:
    return save({**load(), **patch})


def add_learned(tags: list[str]) -> dict:
    p = load()
    seen = {t.lower() for t in p["learned"]}
    for t in tags:
        t = t.strip().lower()
        if t and t not in seen:
            p["learned"].append(t)
            seen.add(t)
    return save(p)


def remember_basket(items: list[str], best_total: float | None, saved: float | None,
                    winners: dict[str, int] | None = None) -> None:
    p = load()
    p["history"].append({"t": int(time.time()), "items": items[:30], "total": best_total, "saved": saved,
                         "winners": winners or {}})
    save(p)


_FILLER = {"large", "small", "medium", "whole", "ground", "extra", "virgin", "fresh", "white", "brown", "count",
           "pack", "gallon", "loaf", "organic", "grade", "free", "cage", "range", "pound", "ounce", "liter", "with",
           "and", "the", "lean", "unsalted", "salted", "original", "classic", "roast", "half", "dozen"}


def keywords(item: str) -> list[str]:
    """The product words in a list line: 'large eggs 12 count' -> ['eggs']."""
    import re

    return [w for w in re.findall(r"[a-z]{3,}", item.lower()) if w not in _FILLER]


_NEEDS_QUALIFIER = {"oil", "juice", "sauce", "water", "powder", "cream", "butter", "beans"}


def staple_name(item: str) -> str | None:
    """The short name a staple is tracked under: 'large eggs 12 count' -> 'eggs',
    'extra virgin olive oil' -> 'olive oil' (plain 'oil' would also match crude oil news)."""
    words = keywords(item)
    if not words:
        return None
    if words[-1] in _NEEDS_QUALIFIER and len(words) >= 2:
        return " ".join(words[-2:])
    return words[-1]


def patterns(p: dict | None = None) -> dict:
    """What the history shows. Computed, never guessed: counts over past baskets."""
    p = p or load()
    history = p["history"]
    counts: dict[str, int] = {}
    wins: dict[str, int] = {}
    organic = total_items = 0
    for h in history:
        for item in h.get("items", []):
            total_items += 1
            organic += "organic" in item.lower()
            k = staple_name(item)
            if k:
                counts[k] = counts.get(k, 0) + 1
        for store, n in (h.get("winners") or {}).items():
            wins[store] = wins.get(store, 0) + n
    staples = [k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))][:8]
    totals = [h["total"] for h in history if h.get("total")]
    saved = [h["saved"] for h in history if h.get("saved")]
    best_store = max(wins.items(), key=lambda kv: kv[1]) if wins else None
    return {
        "baskets": len(history),
        "staples": staples,
        "staple_counts": {k: counts[k] for k in staples},
        "cheapest_store": {"store": best_store[0], "wins": best_store[1], "of": sum(wins.values())} if best_store else None,
        "average_basket": round(sum(totals) / len(totals), 2) if totals else None,
        "saved_so_far": round(sum(saved), 2) if saved else 0.0,
        "organic_share": round(organic / total_items, 2) if total_items else 0.0,
        "habits": p["learned"],
    }


def watchwords(p: dict | None = None) -> list[str]:
    """Words used to tell whether a headline touches this shopper's basket."""
    words = list(patterns(p)["staples"])
    return [w[:-1] if w.endswith("s") and not w.endswith("ss") and len(w) > 3 and " " not in w else w for w in words]


def touches(text: str, p: dict | None = None) -> list[str]:
    """Which of this shopper's staples a piece of text mentions."""
    import re

    low = text.lower()
    return [w for w in watchwords(p) if re.search(rf"\b{re.escape(w)}", low)]


def reset() -> dict:
    try:
        PATH.unlink()
    except FileNotFoundError:
        pass
    return load()


def as_text(p: dict | None = None) -> str:
    p = p or load()
    bits = []
    if p["organic_only"]:
        bits.append("organic products only")
    bits.append("store brands are fine" if p["store_brands_ok"] else "name brands only, no store brands")
    if p["diet"]:
        bits.append(f"diet: {p['diet']}")
    if p["learned"]:
        bits.append("habits seen on past receipts: " + ", ".join(p["learned"]))
    pat = patterns(p)
    if pat["staples"]:
        bits.append("buys most often: " + ", ".join(pat["staples"][:6]))
    return "; ".join(bits)
