# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the Studio media engine — offline behavior, cache, budget, ledger.

These never hit the network: with no provider keys the Studio must still
produce a still (the engraver), skip audio/video gracefully, cache by content,
and honor the budget ledger.
"""

import json

import pytest

from ante.ai import studio as st
from ante.config import AnteConfig


@pytest.fixture
def offline_studio(tmp_path, monkeypatch):
    for var in (
        "HF_KEY", "HF_API_KEY", "HF_API_SECRET",
        "ELEVENLABS_API_KEY", "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    return st.Studio(tmp_path, force_offline=True, now=1_700_000_000.0)


def test_still_offline_writes_svg_plate(offline_studio):
    ref = offline_studio.still({"prompt": "a lantern", "title": "Enzymes"})
    assert ref.kind == "still"
    assert ref.provider == "offline-engraver"
    p = offline_studio.path_of(ref)
    assert p.is_file() and p.suffix == ".svg"
    assert "<svg" in p.read_text()


def test_still_is_content_addressed_and_cached(offline_studio):
    a = offline_studio.still({"prompt": "same", "title": "T"})
    b = offline_studio.still({"prompt": "same", "title": "T"})
    assert a.key == b.key
    c = offline_studio.still({"prompt": "different", "title": "T"})
    assert c.key != a.key


def test_offline_motion_and_speech_absent(offline_studio):
    still = offline_studio.still({"prompt": "x", "title": "X"})
    assert offline_studio.motion({"motion": "drift"}, still) is None
    assert offline_studio.speech("hello", "night") is None
    assert offline_studio.transcribe(b"audio-bytes") is None


def test_offline_plate_is_deterministic():
    spec = {"title": "Glycolysis", "caption": "ten steps", "anchors": [{"object": "a torch"}]}
    assert st.render_offline_plate(spec, "k1") == st.render_offline_plate(spec, "k1")
    assert st.render_offline_plate(spec, "k1") != st.render_offline_plate(spec, "k2")


def test_plate_escapes_markup():
    svg = st.render_offline_plate({"title": "<script>", "caption": "a & b"}, "k")
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
    assert "a &amp; b" in svg


def test_budget_blocks_when_capped(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_KEY", raising=False)
    cfg = AnteConfig(studio_daily_cap=2, studio_monthly_cap=10)
    s = st.Studio(tmp_path, cfg=cfg, now=1_700_000_000.0)
    assert s.budget()["allowed"]
    s._spend(2)
    assert not s.budget()["allowed"]
    assert s.budget()["daily_used"] == 2


def test_ledger_persists_across_instances(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_KEY", raising=False)
    s1 = st.Studio(tmp_path, force_offline=True, now=1_700_000_000.0)
    ref = s1.still({"prompt": "persist", "title": "P"})
    s2 = st.Studio(tmp_path, force_offline=True, now=1_700_000_000.0)
    assert s2.lookup("still", {"prompt": "persist", "title": "P"}) is not None
    assert s2.assets("still") and s2.assets("still")[0].key == ref.key


def test_status_reports_providers_and_counts(offline_studio):
    offline_studio.still({"prompt": "one", "title": "1"})
    status = offline_studio.status()
    assert status["providers"]["offline_only"] is True
    assert status["assets"]["still"] == 1


def test_spoken_lines_splits_on_sentences():
    text = "First sentence here. " + "word " * 60 + "end. Short tail."
    lines = st.spoken_lines(text, max_words=42)
    assert len(lines) >= 2
    assert all(line.strip() for line in lines)


def test_multipart_encoder_shapes_body():
    body, ctype = st._multipart({"model": "m"}, "file", "a.webm", "audio/webm", b"xyz")
    assert ctype.startswith("multipart/form-data; boundary=")
    assert b'name="model"' in body
    assert b'filename="a.webm"' in body
    assert b"xyz" in body


def test_providers_detected_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk-test")
    monkeypatch.delenv("HF_KEY", raising=False)
    monkeypatch.delenv("HF_API_KEY", raising=False)
    s = st.Studio(tmp_path)
    assert s.providers()["elevenlabs"] is True
    assert s.providers()["higgsfield"] is False
