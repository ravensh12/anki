# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Combined application performance = multiple-choice + open-ended, pooled.

Mastery and readiness are shown from application evidence. That evidence now has
two sources — recognition items (``performance_items``) and produced-answer items
(``openended``) — so this module pools them per topic into one (point, low, high)
accuracy with an honest Wilson interval over the total item count. One signal, two
kinds of proof, both harder to fake than a flashcard self-rating.
"""

from __future__ import annotations

from collections.abc import Mapping

from .memory import wilson_interval
from .openended import topic_open_counts
from .performance_items import MIN_ITEMS_FOR_EVIDENCE, topic_application_counts


def combined_topic_performance(
    mcq_responses: Mapping[str, object] | None,
    open_responses: Mapping[str, object] | None = None,
) -> dict[str, tuple[float, float, float]]:
    """Per-topic (point, low, high) application accuracy pooling MCQ correctness
    and open-ended partial-credit scores. Topics with no evidence are omitted so
    downstream mastery/readiness stay honest."""
    pooled: dict[str, list[float]] = {}  # topic -> [score_sum, n]
    for topic, (correct, n) in topic_application_counts(mcq_responses or {}).items():
        entry = pooled.setdefault(topic, [0.0, 0.0])
        entry[0] += correct
        entry[1] += n
    for topic, (score_sum, n) in topic_open_counts(open_responses or {}).items():
        entry = pooled.setdefault(topic, [0.0, 0.0])
        entry[0] += score_sum
        entry[1] += n

    out: dict[str, tuple[float, float, float]] = {}
    for topic, vals in pooled.items():
        score_sum, n = vals[0], int(vals[1])
        if n < MIN_ITEMS_FOR_EVIDENCE:
            continue
        point = score_sum / n
        lo, hi = wilson_interval(round(point * n), n)
        out[topic] = (round(point, 4), round(lo, 4), round(hi, 4))
    return out
