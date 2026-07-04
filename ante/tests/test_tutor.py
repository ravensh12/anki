# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the table tutor (ask Sahir about the card just played)."""

from ante.ai.provider import OfflineProvider
from ante.ai.tutor import _MAX_TURNS, build_prompt, tutor_reply


class _FakeLLM:
    name = "fake-llm"

    def __init__(self, reply="Because lysine's side chain is protonated at pH 7.4."):
        self._reply = reply
        self.calls = []

    def complete(self, system, user, max_tokens=400):
        self.calls.append((system, user, max_tokens))
        return self._reply

    def answer(self, question, context):  # Provider protocol
        return context


def test_tutor_uses_the_llm_and_reports_provider():
    p = _FakeLLM()
    out = tutor_reply(
        front="Which amino acids are basic?",
        back="Lys, Arg, His",
        topic="mcat::bio_biochem::amino_acids",
        history=[],
        question="Why is lysine positive?",
        provider=p,
    )
    assert out["ok"] and out["available"]
    assert "lysine" in out["reply"].lower()
    assert out["provider"] == "fake-llm"
    system, user, _ = p.calls[0]
    # the card and the question both reach the prompt; the persona stays fixed
    assert "Lys, Arg, His" in user and "Why is lysine positive?" in user
    assert "Sahir" in system


def test_tutor_prompt_carries_bounded_history():
    history = [{"role": "student", "text": f"q{i}"} for i in range(30)]
    prompt = build_prompt("F", "B", "t", history, "final?")
    assert "q29" in prompt and "q0" not in prompt  # capped at the last turns
    assert prompt.count("Student:") <= _MAX_TURNS + 1


def test_tutor_offline_is_honest_not_fake():
    out = tutor_reply(
        front="What does a competitive inhibitor do to Km?",
        back="It raises the apparent Km; Vmax is unchanged.",
        topic="mcat::bio_biochem::enzymes",
        history=[],
        question="What happens to Km?",
        provider=OfflineProvider(),
    )
    assert out["ok"] and not out["available"]
    # quotes the card itself and says the full tutor is off — never pretends
    assert "Km" in out["reply"]
    assert "ANTHROPIC_API_KEY" in out["reply"]


def test_tutor_rejects_empty_questions():
    assert tutor_reply("F", "B", "t", [], "  ", provider=_FakeLLM())["ok"] is False
