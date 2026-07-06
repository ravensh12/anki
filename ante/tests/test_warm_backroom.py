# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""The warm plan must speak with exactly the runtime's voice: every line the
Back Room can produce on the deterministic path appears in the plan verbatim,
so the Studio's content-addressed cache turns a live exam into pure hits."""

from ante.openended import OpenItem
from ante.tools.warm_backroom import default_topics, plan_topic_lines
from ante.viva import (
    failed_line,
    opening_line,
    passed_line,
    template_probe,
)

TOPIC = "mcat::test::warmth"


def _item(points, id_="w1"):
    return OpenItem(
        id=id_,
        topic=TOPIC,
        prompt="Explain warmth.",
        model_answer="Warmth flows downhill through kindness and enzymes.",
        rubric_points=tuple(points),
        keywords=("warmth",),
        difficulty=0.5,
    )


def test_plan_covers_opening_probes_and_verdicts():
    items = (_item(["kindness", "enzymes"]),)
    lines = plan_topic_lines(TOPIC, items, name="Warmth")

    assert opening_line("Warmth") in lines
    assert passed_line("Warmth") in lines
    # one probe per rubric point + the generic deep-dive probe
    assert template_probe("kindness") in lines
    assert template_probe("enzymes") in lines
    assert template_probe("") in lines
    # every reachable missing[:2] verdict: singles, the ordered pair, the fallback
    assert failed_line(["kindness"]) in lines
    assert failed_line(["enzymes"]) in lines
    assert failed_line(["kindness", "enzymes"]) in lines
    assert failed_line([]) in lines


def test_plan_is_deduplicated():
    items = (
        _item(["kindness", "enzymes"]),
        _item(["kindness"], id_="w2"),  # a shared rubric point across items
    )
    lines = plan_topic_lines(TOPIC, items, name="Warmth")
    assert len(lines) == len(set(lines)), "warm plan must not render a line twice"


def test_plan_ignores_other_topics():
    items = (
        _item(["kindness"]),
        OpenItem(
            id="other",
            topic="mcat::test::cold",
            prompt="Explain cold.",
            model_answer="",
            rubric_points=("ice",),
            keywords=(),
            difficulty=0.5,
        ),
    )
    lines = plan_topic_lines(TOPIC, items, name="Warmth")
    assert template_probe("ice") not in lines


def test_default_topics_come_from_the_bank():
    """The tool's default topics are real bank topics (what the demo Back Room
    suggests), so a no-arg warm run pre-renders the lines the tour will hit."""
    from ante.openended import load_open_items

    bank = {it.topic for it in load_open_items()}
    if not bank:
        return  # bank not built in this environment
    topics = default_topics()
    assert topics, "expected at least one default topic"
    assert set(topics) <= bank
