# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Leakage check: flag test items (or near-copies) that slipped into training.

Leaked data makes a model look smarter than it is and, per the spec, zeroes that
score. This scans a training set against a held-out test set and flags exact and
near-duplicate items by normalized-text Jaccard over word shingles. A token
inverted index keeps it from being quadratic on large sets.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

_WORD = re.compile(r"[a-z0-9]+")


def normalize(text: str) -> str:
    return " ".join(_WORD.findall(text.lower()))


def shingles(text: str, k: int = 3) -> set[str]:
    """Word k-shingles; falls back to the token set for short texts."""
    words = normalize(text).split()
    if len(words) < k:
        return set(words)
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b)


@dataclass(frozen=True)
class Leak:
    test_index: int
    train_index: int
    score: float
    test_text: str
    train_text: str

    def as_dict(self) -> dict:
        return {
            "test_index": self.test_index,
            "train_index": self.train_index,
            "score": round(self.score, 4),
            "test_text": self.test_text,
            "train_text": self.train_text,
        }


def find_leaks(
    train: Sequence[str],
    test: Sequence[str],
    threshold: float = 0.85,
    k: int = 3,
) -> list[Leak]:
    train_norm = [normalize(t) for t in train]
    train_shingles = [shingles(t, k) for t in train]

    # exact-normalized index for O(1) exact hits
    exact: dict[str, list[int]] = defaultdict(list)
    for i, tn in enumerate(train_norm):
        exact[tn].append(i)

    # token -> training docs, to limit near-dup candidates
    token_index: dict[str, list[int]] = defaultdict(list)
    for i, sh in enumerate(train_shingles):
        for s in sh:
            token_index[s].append(i)

    leaks: list[Leak] = []
    for ti, t in enumerate(test):
        tn = normalize(t)
        if tn in exact:
            tr = exact[tn][0]
            leaks.append(Leak(ti, tr, 1.0, t, train[tr]))
            continue
        sh = shingles(t, k)
        candidates: set[int] = set()
        for s in sh:
            candidates.update(token_index.get(s, ()))
        best_idx, best_score = -1, 0.0
        for c in candidates:
            sc = jaccard(sh, train_shingles[c])
            if sc > best_score:
                best_idx, best_score = c, sc
        if best_idx >= 0 and best_score >= threshold:
            leaks.append(Leak(ti, best_idx, best_score, t, train[best_idx]))
    return leaks


def _load_items(path: Path) -> list[str]:
    """Load a list of strings from a JSON file. Accepts a list of strings, or a
    list of objects with 'front'/'back' or 'question'/'answer' fields."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    items: list[str] = []
    for it in raw:
        if isinstance(it, str):
            items.append(it)
        elif isinstance(it, dict):
            q = it.get("front") or it.get("question") or ""
            a = it.get("back") or it.get("answer") or ""
            items.append(f"{q} {a}".strip())
    return items


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan for train/test leakage.")
    parser.add_argument("--train", required=True, type=Path)
    parser.add_argument("--test", required=True, type=Path)
    parser.add_argument("--threshold", type=float, default=0.85)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    train = _load_items(args.train)
    test = _load_items(args.test)
    leaks = find_leaks(train, test, args.threshold)

    if args.json:
        print(json.dumps([leak.as_dict() for leak in leaks], indent=2))
    elif not leaks:
        print(f"CLEAN: no leaks found ({len(test)} test vs {len(train)} train).")
    else:
        print(f"LEAKAGE: {len(leaks)} test item(s) found in training data:")
        for leak in leaks:
            print(
                f"  test#{leak.test_index} ~ train#{leak.train_index} "
                f"(score {leak.score:.2f}): {leak.test_text[:60]!r}"
            )
    # non-zero exit on leaks so CI can gate on it
    return 1 if leaks else 0


if __name__ == "__main__":
    raise SystemExit(main())
