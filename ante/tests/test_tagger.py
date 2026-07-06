# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the untagged-card -> Ante topic mapper (ante/tagger.py)."""

from __future__ import annotations

from ante.outline import load_outline
from ante.tagger import MIN_MARGIN, MIN_SCORE, match_topic


def test_confident_matches_land_on_the_right_table():
    cases = {
        "mcat::bio_biochem::enzymes": (
            "What does a competitive inhibitor do to Km?",
            "Km increases; Vmax is unchanged (Michaelis-Menten kinetics).",
        ),
        "mcat::bio_biochem::glycolysis": (
            "Rate-limiting enzyme of glycolysis?",
            "Phosphofructokinase-1 (PFK-1).",
        ),
        "mcat::chem_phys::acids_bases": (
            "Henderson-Hasselbalch equation is used for what?",
            "Relating pH, pKa and buffer ratio in a titration.",
        ),
        "mcat::chem_phys::fluids": (
            "State Bernoulli's principle.",
            "Faster fluid flow means lower pressure; hydrostatic terms balance.",
        ),
        "mcat::psych_soc::learning": (
            "Operant conditioning vs classical conditioning?",
            "Skinner's reinforcement shapes behavior; Pavlov pairs stimuli.",
        ),
    }
    for expected, (front, back) in cases.items():
        m = match_topic(front, back)
        assert m is not None, f"expected a match for {expected}"
        assert m.tag == expected


def test_ambiguous_or_thin_cards_stay_unlisted():
    # generic study card with no distinctive MCAT content
    assert match_topic("Define the term.", "It is important.") is None
    # empty
    assert match_topic("", "") is None
    # a single weak hit under the score floor
    weak = match_topic("A question about the brain.", "")
    assert weak is None or weak.score >= MIN_SCORE


def test_deck_name_is_context_not_proof():
    # deck name alone shouldn't seat a contentless card
    assert match_topic("front", "back", deck_name="MCAT Biochem") is None


def test_cars_is_never_auto_seated():
    # CARS carries no content keywords by design
    m = match_topic(
        "Read the passage and identify the author's main argument.",
        "The tone suggests skepticism.",
    )
    assert m is None or not m.tag.endswith("::cars")


def test_returned_tag_is_a_real_outline_topic():
    outline = load_outline()
    valid = set(outline.all_topics())
    m = match_topic("Describe the electron transport chain and ATP synthase.", "")
    assert m is not None
    assert m.tag in valid


def test_thresholds_are_sane():
    assert MIN_SCORE >= 1.0
    assert MIN_MARGIN >= 0.0


def test_recognized_deck_taxonomy_maps_directly():
    # MileDown tags its cards with its own topic hierarchy; trust it directly
    # even when the card text alone would be ambiguous (e.g. a bare equation).
    cases = {
        "mcat::chem_phys::atomic_structure": "MileDown::General_Chemistry::Atomic_Structure",
        "mcat::chem_phys::acids_bases": "MileDown::General_Chemistry::Acids_and_Bases",
        "mcat::chem_phys::circuits": "MileDown::Physics::Electrostatics",
        "mcat::chem_phys::fluids": "MileDown::Physics::Fluids",
        "mcat::bio_biochem::nucleic_acids": "MileDown::Biochemistry::DNA_and_RNA",
        "mcat::bio_biochem::enzymes": "MileDown::Biochemistry::Enzymes",
        "mcat::psych_soc::memory": "MileDown::Behavioral::Memory",
        "mcat::psych_soc::social_thinking": "MileDown::Behavioral::Social::Social_Behavior",
    }
    for expected, tag in cases.items():
        m = match_topic("", "", tags=[tag])
        assert m is not None, f"expected a taxonomy match for {tag}"
        assert m.tag == expected, f"{tag} -> {m.tag}, wanted {expected}"


def test_deepest_recognized_segment_wins():
    # the leaf 'Brain' isn't mapped, so it falls back to 'Biology_and_Behavior'
    m = match_topic(
        "", "", tags=["MileDown::Behavioral::Biology_and_Behavior::Brain"]
    )
    assert m is not None
    assert m.tag == "mcat::psych_soc::biological_behavior"


def test_unrecognized_deck_root_ignored():
    # a random deck's tags aren't a taxonomy we trust; fall back to keywords
    m = match_topic("", "", tags=["RandomDeck::Chapter1::Topic"])
    assert m is None
