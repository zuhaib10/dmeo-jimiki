"""Concise, factual, SEO-friendly product names: [colour]-[feature]-[product type]."""
from __future__ import annotations

import re

MARKETING_WORDS = {
    "beautiful", "elegant", "stunning", "exquisite", "gorgeous", "luxury", "luxurious", "premium", "royal",
    "trendy", "designer", "classic", "charming", "lovely", "perfect", "unique", "exclusive", "fancy", "chic",
    "glamorous", "timeless", "traditional", "ethnic", "stylish", "fashionable", "delicate", "dazzling", "amazing",
    "graceful", "statement", "bridal", "party", "wear", "women", "womens", "girls", "new", "latest", "best",
    "handcrafted", "handmade", "authentic", "genuine", "real", "pure", "fine", "a", "an", "the", "with", "and", "of",
}
# Materials that cannot be verified from a photograph are replaced by appearance words.
UNVERIFIABLE = {
    "ruby": "red-stone", "rubies": "red-stone", "emerald": "green-stone", "emeralds": "green-stone",
    "sapphire": "blue-stone", "sapphires": "blue-stone", "diamond": "crystal", "diamonds": "crystal",
    "gold": "gold-tone", "golden": "gold-tone", "silver": "silver-tone", "platinum": "silver-tone",
    "22k": "", "18k": "", "24k": "", "sterling": "", "925": "", "polki": "stone", "kundan": "stone",
}
MAX_TOKENS = 6
MAX_LEN = 60


def _tokens(text: str | None) -> list[str]:
    if not text:
        return []
    text = text.lower().replace("&", " ")
    raw = [t for t in re.split(r"[^a-z0-9]+", text) if t]
    out: list[str] = []
    i = 0
    while i < len(raw):
        t = raw[i]
        # keep "gold tone" / "silver tone" as already-safe phrases
        if t in ("gold", "silver", "rose") and i + 1 < len(raw) and raw[i + 1] == "tone":
            out += [t, "tone"]
            i += 2
            continue
        if t in UNVERIFIABLE:
            out += [x for x in UNVERIFIABLE[t].split("-") if x]
        elif t not in MARKETING_WORDS:
            out.append(t)
        i += 1
    return out


def build_name(colour: str | None, feature: str | None, product_type: str | None) -> str:
    parts = [_tokens(colour), _tokens(feature), _tokens(product_type) or ["jewellery"]]
    tokens: list[str] = []
    for part in parts:
        # skip a phrase that is already fully contained (e.g. colour "gold-tone" + feature "gold-tone")
        if part and all(t in tokens for t in part):
            continue
        tokens += [t for t in part if not (tokens and tokens[-1] == t)]
    # the product type is the most important — trim from the middle if too long
    while len(tokens) > MAX_TOKENS:
        tokens.pop(len(tokens) // 2 - 1 if len(tokens) > 2 else 0)
    name = "-".join(tokens)
    while len(name) > MAX_LEN and "-" in name:
        name = name.split("-", 1)[1]
    return name or "jewellery"


def folder_name(number: int, name: str) -> str:
    return f"{number} - {name}"


def file_stem(number: int, name: str) -> str:
    return f"{number}-{name}"
