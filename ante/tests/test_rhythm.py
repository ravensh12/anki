# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for Peak Hours (circadian performance)."""

from ante.rhythm import peak_windows


def test_abstains_without_enough_reviews():
    rep = peak_windows([(9, 1)] * 5)
    assert rep["available"] is False
    assert rep["n"] == 5


def test_finds_best_window():
    data = []
    # morning: 20 reviews, 90% correct
    for i in range(20):
        data.append((9, 1 if i < 18 else 0))
    # night: 20 reviews, 40% correct
    for i in range(20):
        data.append((23, 1 if i < 8 else 0))
    rep = peak_windows(data)
    assert rep["available"] is True
    assert rep["best_window"] == "morning"
    assert rep["advice"] and "morning" in rep["advice"]
    # windows sorted best-first
    accs = [w["accuracy"] for w in rep["windows"]]
    assert accs == sorted(accs, reverse=True)


def test_window_assignment_wraps_midnight():
    # 2am should count as night; build a night-heavy set
    data = [(2, 1)] * 20 + [(14, 0)] * 20
    rep = peak_windows(data)
    names = {w["window"] for w in rep["windows"]}
    assert "night" in names
    assert rep["best_window"] == "night"


def test_delta_is_relative_to_overall():
    data = [(9, 1)] * 20 + [(23, 0)] * 20  # morning perfect, night zero
    rep = peak_windows(data)
    morning = next(w for w in rep["windows"] if w["window"] == "morning")
    assert morning["delta"] > 0  # above the 50% overall
