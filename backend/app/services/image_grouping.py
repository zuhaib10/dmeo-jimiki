"""Decide which photographs show the same physical product.

Conservative by design: when evidence is uncertain, photographs stay as
separate products (a wrong merge is much harder to undo than a split).

Signals combined:
* deterministic similarity — foreground colour histogram, dominant colours,
  perceptual/difference hash of the product crop, ORB keypoint geometry
* AI vision partitioning of the batch (category, shape, stone arrangement,
  structure, design features) when a vision provider is configured
* comparison with existing product records (so a new angle of an existing
  product attaches to it instead of creating a duplicate product)

These functions operate on plain records so the live pipeline and the
dry-run scan share exactly the same logic.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Hashable

import cv2
import numpy as np

from .features import colour_similarity, is_near_duplicate, key_colours, orb_features, pair_similarity

log = logging.getLogger("jimiki.grouping")

# Without AI, only near-identical shots (bursts / re-exports) are merged. Real JIMIKI photographs
# share the same branded display card and props, which makes different products look alike to
# colour/keypoint features (the card logo matches perfectly), so visual similarity alone is never
# trusted to merge or attach products.
CANDIDATE_THRESHOLD = 0.40            # below this an existing product is not even sent for AI verification
COLOUR_VETO = 0.35                     # AI group member is split off below this colour agreement


@dataclass
class ImageRecord:
    key: Hashable
    path: Path
    file_hash: str
    features: dict[str, Any]
    _orb: Any = field(default=None, repr=False)

    def orb(self) -> Any:
        if self._orb is None:
            try:
                self._orb = orb_features(self.path, self.file_hash)
            except Exception as exc:  # noqa: BLE001
                log.warning("ORB extraction failed for %s: %s", self.path, exc)
                self._orb = (np.zeros((0, 2), np.float32), None)
        return self._orb


@dataclass
class ExistingProduct:
    pk: int
    code: str
    label: str
    records: list[ImageRecord]


@dataclass
class GroupDecision:
    keys: list[Hashable]
    confidence: float
    reason: str
    method: str
    existing_product_pk: int | None = None
    pair_scores: dict[str, Any] = field(default_factory=dict)


def _near_duplicate(a: ImageRecord, b: ImageRecord) -> bool:
    return is_near_duplicate(a.features, b.features)


def similarity_matrix(records: list[ImageRecord]) -> dict[tuple[int, int], dict[str, float]]:
    sims: dict[tuple[int, int], dict[str, float]] = {}
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            sims[(i, j)] = pair_similarity(records[i].features, records[j].features,
                                           records[i].orb(), records[j].orb())
    return sims


def _sim(sims: dict[tuple[int, int], dict[str, float]], i: int, j: int) -> dict[str, float]:
    return sims[(i, j)] if i < j else sims[(j, i)]


def complete_linkage(n: int, sims: dict[tuple[int, int], dict[str, float]], threshold: float) -> list[list[int]]:
    """Agglomerative clustering where EVERY pair in a cluster must clear the threshold."""
    clusters = [[i] for i in range(n)]

    def ok(i: int, j: int) -> bool:
        s = _sim(sims, i, j)
        return s["score"] >= threshold or bool(s["near_duplicate"])

    def linkage(a: list[int], b: list[int]) -> float:
        return min(_sim(sims, i, j)["score"] if not _sim(sims, i, j)["near_duplicate"] else 1.0 for i in a for j in b)

    merged = True
    while merged and len(clusters) > 1:
        merged = False
        best: tuple[float, int, int] | None = None
        for x in range(len(clusters)):
            for y in range(x + 1, len(clusters)):
                if all(ok(i, j) for i in clusters[x] for j in clusters[y]):
                    link = linkage(clusters[x], clusters[y])
                    if best is None or link > best[0]:
                        best = (link, x, y)
        if best:
            _, x, y = best
            clusters[x] = clusters[x] + clusters[y]
            del clusters[y]
            merged = True
    return clusters


def _mean_pair(sims: dict[tuple[int, int], dict[str, float]], members: list[int], key: str = "score") -> float:
    vals = [_sim(sims, i, j)[key] for a, i in enumerate(members) for j in members[a + 1:]]
    return float(np.mean(vals)) if vals else 1.0


def _ai_partition(records: list[ImageRecord], vision: Any) -> list[tuple[list[int], float, str]]:
    out: list[tuple[list[int], float, str]] = []
    for start in range(0, len(records), 16):
        chunk = records[start:start + 16]
        part = vision.partition([r.path for r in chunk])
        seen: set[int] = set()
        for g in part.groups:
            idx = [n - 1 + start for n in g.image_numbers if 1 <= n <= len(chunk) and (n - 1 + start) not in seen]
            seen.update(idx)
            if idx:
                out.append((idx, max(0.0, min(1.0, float(g.confidence))), g.reason))
        for i in range(start, start + len(chunk)):
            if i not in seen:
                out.append(([i], 1.0, "Not assigned by AI; kept separate"))
    return out


def group_batch(records: list[ImageRecord], vision: Any | None, confidence_threshold: float,
                notes: list[str] | None = None) -> list[GroupDecision]:
    """Partition a batch of new photographs into product groups."""
    notes = notes if notes is not None else []
    n = len(records)
    if n == 0:
        return []
    if n == 1:
        return [GroupDecision([records[0].key], 1.0, "Single photograph in batch", "single")]

    sims = similarity_matrix(records)
    decisions: list[GroupDecision] = []

    ai_groups = None
    if vision is not None:
        try:
            ai_groups = _ai_partition(records, vision)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"AI grouping unavailable ({exc}); used deterministic grouping")
            log.warning("AI partition failed: %s", exc)

    if ai_groups is not None:
        for members, ai_conf, reason in ai_groups:
            if len(members) == 1:
                decisions.append(GroupDecision([records[members[0]].key], 1.0,
                                               f"Distinct product (AI): {reason}", "ai"))
                continue
            if ai_conf < confidence_threshold:
                for m in members:
                    decisions.append(GroupDecision(
                        [records[m].key], round(1 - ai_conf, 3),
                        f"Kept separate — AI same-product confidence {ai_conf:.2f} below threshold "
                        f"{confidence_threshold:.2f} ({reason})", "ai-conservative"))
                continue
            # Deterministic veto: split off members whose colours clearly disagree with the rest.
            kept, vetoed = list(members), []
            changed = True
            while changed and len(kept) > 1:
                changed = False
                scores = {m: np.mean([colour_similarity(key_colours(records[m].features["dominant"]),
                                                        key_colours(records[o].features["dominant"]))
                                      for o in kept if o != m]) for m in kept}
                worst = min(scores, key=lambda m: scores[m])
                if scores[worst] < COLOUR_VETO:
                    kept.remove(worst)
                    vetoed.append(worst)
                    changed = True
            det = _mean_pair(sims, kept)
            conf = round(0.75 * ai_conf + 0.25 * det, 3) if len(kept) > 1 else 1.0
            decisions.append(GroupDecision(
                [records[m].key for m in kept], conf,
                f"AI: {reason} (AI confidence {ai_conf:.2f}; visual similarity {det:.2f})", "ai+visual",
                pair_scores={"mean_visual": round(det, 3), "ai": ai_conf}))
            for m in vetoed:
                decisions.append(GroupDecision([records[m].key], 0.6,
                                               "Separated from AI group — colour profile inconsistent", "visual-veto"))
        return decisions

    if vision is None:
        notes.append("AI vision not configured — only near-identical shots are grouped; "
                     "other photographs are kept as separate products")
    for cluster in complete_linkage(n, sims, threshold=2.0):  # 2.0 = never by score, only near-duplicates
        if len(cluster) == 1:
            decisions.append(GroupDecision([records[cluster[0]].key], 1.0,
                                           "Kept as its own product (deterministic mode merges only near-identical shots)",
                                           "visual"))
        else:
            decisions.append(GroupDecision([records[i].key for i in cluster], 0.95,
                                           "Near-identical shots of the same product (perceptual hash match)",
                                           "near-duplicate"))
    return decisions


def match_existing(group: list[ImageRecord], existing: list[ExistingProduct], vision: Any | None,
                   confidence_threshold: float) -> tuple[ExistingProduct | None, float, str]:
    """Return the existing product this group belongs to, if confidently the same physical item."""
    if not existing:
        return None, 0.0, ""
    # Cheap pre-filter on colour/histogram, then full similarity on the best few products.
    cheap: list[tuple[float, ExistingProduct]] = []
    for prod in existing:
        best = 0.0
        for g in group:
            for r in prod.records:
                ha = np.array(g.features["hist"], np.float32)
                hb = np.array(r.features["hist"], np.float32)
                c = 0.5 * max(0.0, float(cv2.compareHist(ha, hb, cv2.HISTCMP_CORREL))) + \
                    0.5 * colour_similarity(g.features["dominant"], r.features["dominant"])
                if _near_duplicate(g, r):
                    c = 2.0
                best = max(best, c)
        cheap.append((best, prod))
    cheap.sort(key=lambda t: -t[0])

    scored: list[tuple[float, bool, ExistingProduct]] = []
    for _, prod in cheap[:8]:
        best, dup = 0.0, False
        for g in group:
            for r in prod.records:
                if _near_duplicate(g, r):
                    dup = True
                s = pair_similarity(g.features, r.features, g.orb(), r.orb())["score"]
                best = max(best, s)
        scored.append((best, dup, prod))
    scored.sort(key=lambda t: (-int(t[1]), -t[0]))

    for score, dup, prod in scored[:3]:
        if dup:
            return prod, 0.97, f"Near-identical photograph of existing product {prod.code}"
        if score < CANDIDATE_THRESHOLD:
            continue
        if vision is not None:
            try:
                m = vision.same_product([g.path for g in group], [r.path for r in prod.records])
            except Exception as exc:  # noqa: BLE001
                log.warning("AI existing-product match failed: %s", exc)
                m = None
            if m is not None and m.same_product and m.confidence >= confidence_threshold:
                conf = round(0.75 * m.confidence + 0.25 * score, 3)
                return prod, conf, f"Matches existing product {prod.code} (AI {m.confidence:.2f}: {m.reason}; visual {score:.2f})"
    return None, 0.0, ""
