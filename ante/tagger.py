# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Map an untagged flashcard to an Ante MCAT topic (``mcat::section::topic``).

Third-party MCAT decks (AnKing, MilesDown, etc.) don't carry Ante's topic
tags, so the Circuit lists every table as ``unlisted`` and the den tells the
student to import cards they already have. This module closes that gap: given
a card's text (front/back, its deck name, any existing tags) it returns the
single best-matching outline topic — but only when the match is *confident and
unambiguous*. Otherwise it returns ``None`` and the card stays unlisted, which
is the honest outcome (Principle 4): the Circuit never pretends a card belongs
to a table it can't defend.

Pure logic, deterministic, dependency-free (no Anki import), so it is unit
tested directly and can be reused by any client.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .outline import Outline, load_outline

# A card is only auto-seated when the winning topic clears this hit score AND
# beats the runner-up by this margin — deliberately conservative so a vague
# card is left unlisted rather than mis-seated.
MIN_SCORE = 2.0
MIN_MARGIN = 1.0

_WORD_RE = re.compile(r"[a-z0-9]+")


def _normalize(text: str) -> str:
    """Lowercase, strip HTML/cloze markup, collapse to spaced word tokens."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\{\{c\d+::(.*?)(::.*?)?\}\}", r"\1", text)  # cloze -> answer
    text = text.replace("&nbsp;", " ")
    return " " + " ".join(_WORD_RE.findall(text.lower())) + " "


# Distinctive terms per topic id. Weights: a strongly diagnostic phrase counts
# more than a generic one. Kept curated (not just the topic name) so common
# MCAT phrasing lands on the right table; ambiguous single words are avoided.
# Section id -> {topic id -> [(phrase, weight), ...]}.
_KEYWORDS: dict[str, dict[str, list[tuple[str, float]]]] = {
    "bio_biochem": {
        "amino_acids": [("amino acid", 2), ("peptide", 1.5), ("side chain", 1.5),
                        ("zwitterion", 2), ("isoelectric", 2), ("r group", 1.5)],
        "protein_structure": [("secondary structure", 2), ("tertiary structure", 2),
                              ("quaternary", 2), ("alpha helix", 2), ("beta sheet", 2),
                              ("protein folding", 2), ("denatur", 1.5)],
        "enzymes": [("enzyme", 2), ("michaelis", 2.5), ("km ", 1.5), ("vmax", 2),
                    ("catalys", 1.5), ("inhibitor", 1.5), ("kinetics", 1),
                    ("active site", 1.5), ("cofactor", 1.5)],
        "carbohydrates": [("carbohydrate", 2), ("monosaccharide", 2), ("glycosidic", 2),
                          ("glucose", 1), ("anomer", 2), ("polysaccharide", 2)],
        "lipids": [("lipid", 2), ("fatty acid", 2), ("phospholipid", 2),
                   ("triacylglycerol", 2), ("steroid", 1.5), ("micelle", 1.5)],
        "nucleic_acids": [("nucleic acid", 2), ("nucleotide", 2), ("base pair", 1.5),
                          ("purine", 2), ("pyrimidine", 2), ("double helix", 1.5)],
        "dna_replication": [("dna replication", 3), ("okazaki", 2.5), ("helicase", 2),
                            ("polymerase", 1.5), ("leading strand", 2), ("lagging strand", 2),
                            ("primase", 2)],
        "transcription_translation": [("transcription", 2), ("translation", 2),
                                       ("mrna", 1.5), ("trna", 2), ("ribosome", 1.5),
                                       ("codon", 2), ("spliceosome", 2)],
        "gene_regulation": [("gene expression", 2), ("gene regulation", 2.5),
                            ("operon", 2.5), ("lac operon", 3), ("transcription factor", 2),
                            ("epigenetic", 2)],
        "glycolysis": [("glycolysis", 3), ("pyruvate", 1.5), ("hexokinase", 2),
                       ("phosphofructokinase", 2.5), ("substrate level phosphorylation", 2)],
        "citric_acid_cycle": [("citric acid cycle", 3), ("krebs", 3), ("tca cycle", 3),
                              ("acetyl coa", 2), ("oxaloacetate", 2), ("isocitrate", 2)],
        "oxidative_phosphorylation": [("oxidative phosphorylation", 3),
                                      ("electron transport chain", 3), ("chemiosmo", 2.5),
                                      ("atp synthase", 2.5), ("proton gradient", 2)],
        "cell_biology": [("cell membrane", 2), ("plasma membrane", 2), ("organelle", 2),
                         ("mitochondri", 1.5), ("cytoskeleton", 2), ("fluid mosaic", 2.5)],
    },
    "chem_phys": {
        "atomic_structure": [("atomic structure", 2), ("quantum number", 2.5),
                             ("electron configuration", 2.5), ("orbital", 1.5),
                             ("isotope", 2), ("aufbau", 2.5)],
        "periodic_trends": [("periodic trend", 3), ("electronegativity", 2),
                            ("ionization energy", 2.5), ("atomic radius", 2.5),
                            ("periodic table", 1.5)],
        "bonding": [("covalent bond", 2), ("ionic bond", 2), ("molecular orbital", 2),
                    ("hybridization", 2.5), ("lewis structure", 2.5), ("vsepr", 3),
                    ("dipole", 1.5)],
        "stoichiometry": [("stoichiometry", 3), ("mole ratio", 2.5), ("limiting reagent", 3),
                          ("empirical formula", 2.5), ("percent yield", 2.5)],
        "thermodynamics": [("thermodynamic", 2), ("enthalpy", 2), ("entropy", 2),
                           ("gibbs free energy", 3), ("hess", 2.5), ("heat capacity", 2)],
        "kinetics": [("reaction rate", 2), ("rate constant", 2.5), ("rate law", 2.5),
                     ("activation energy", 2), ("arrhenius", 2.5), ("catalyst", 1)],
        "equilibrium": [("equilibrium", 2), ("le chatelier", 3), ("equilibrium constant", 2.5),
                        ("keq", 2), ("reaction quotient", 2.5)],
        "acids_bases": [("acid base", 2), ("ph ", 1.5), ("titration", 2.5), ("buffer", 2),
                        ("henderson hasselbalch", 3), ("pka", 2), ("conjugate base", 2)],
        "kinematics": [("kinematic", 2.5), ("velocity", 1.5), ("acceleration", 1.5),
                       ("projectile", 2.5), ("displacement", 2)],
        "force_energy": [("newton", 1.5), ("free body", 2.5), ("work energy", 2),
                         ("kinetic energy", 1.5), ("potential energy", 1.5),
                         ("momentum", 1.5), ("torque", 2)],
        "fluids": [("fluid", 2), ("bernoulli", 3), ("buoyan", 2.5), ("viscosity", 2.5),
                   ("hydrostatic", 2.5), ("poiseuille", 3)],
        "circuits": [("electrostatic", 2), ("coulomb", 2.5), ("capacitor", 2.5),
                     ("resistor", 2), ("ohm", 2), ("circuit", 1.5), ("voltage", 1.5)],
    },
    "psych_soc": {
        "sensation_perception": [("sensation", 2), ("perception", 2), ("sensory", 1.5),
                                 ("weber", 2.5), ("signal detection", 2.5), ("gestalt", 2.5),
                                 ("just noticeable difference", 3)],
        "cognition": [("cognition", 2), ("problem solving", 2), ("heuristic", 2),
                      ("cognitive bias", 2.5), ("intelligence", 1.5), ("piaget", 2.5)],
        "memory": [("memory", 1.5), ("encoding", 2), ("long term memory", 2.5),
                   ("working memory", 2.5), ("retrieval", 1.5), ("amnesia", 2)],
        "learning": [("classical conditioning", 3), ("operant conditioning", 3),
                     ("reinforcement", 2), ("pavlov", 2.5), ("skinner", 2.5),
                     ("habituation", 2)],
        "motivation_emotion": [("motivation", 2), ("emotion", 2), ("drive reduction", 2.5),
                               ("maslow", 2.5), ("james lange", 3), ("yerkes dodson", 3)],
        "identity": [("identity", 2), ("self concept", 2.5), ("self esteem", 2),
                     ("erikson", 2.5), ("self efficacy", 2.5), ("looking glass self", 3)],
        "social_thinking": [("attribution", 2.5), ("stereotype", 2), ("prejudice", 2),
                            ("conformity", 2.5), ("social thinking", 2), ("attitude", 1.5),
                            ("in group", 2)],
        "social_structure": [("social structure", 2.5), ("institution", 2),
                             ("social stratification", 3), ("weber", 1.5), ("durkheim", 2.5),
                             ("bureaucracy", 2)],
        "demographics": [("demographic", 2.5), ("population", 1.5), ("urbanization", 2.5),
                         ("migration", 2), ("fertility rate", 2.5), ("social change", 2)],
        "biological_behavior": [("neurotransmitter", 2), ("nervous system", 1.5),
                                ("brain", 1), ("amygdala", 2.5), ("hippocampus", 2.5),
                                ("dopamine", 2), ("action potential", 2)],
    },
    "cars": {
        # CARS is skills, not content; nothing reliably identifies it from card
        # text, so it is intentionally left with no keywords (never auto-seated).
        "cars": [],
    },
}


# --------------------------------------------------------------------------- #
# Recognized third-party deck taxonomies. When a deck already tags cards with
# its own topic hierarchy (MileDown, AnKing, ...), that path is far higher
# signal than card text, so we map it directly and deterministically. The map
# is intentionally partial: MileDown covers the whole MCAT (organ systems,
# organic reactions, optics, ...) while Ante's outline is a curated subset, so
# topics with no Ante table are left out and those cards stay unlisted rather
# than being forced onto a wrong table.
# --------------------------------------------------------------------------- #

# Recognized deck roots (first tag segment, lowercased).
_DECK_ROOTS = {"miledown", "miledowns", "anking", "ankihub", "ankinghub"}

# MileDown-style topic leaf (lowercased) -> Ante outline topic id. Ante topic
# ids are unique across sections, so the id alone resolves to a full tag.
_DECK_TOPIC: dict[str, str] = {
    # chem / phys
    "atomic_structure": "atomic_structure",
    "nuclear_phenomena": "atomic_structure",
    "periodic_table": "periodic_trends",
    "periodic_trends": "periodic_trends",
    "bonding": "bonding",
    "intermolecular_forces": "bonding",
    "thermochemistry": "thermodynamics",
    "thermodynamics": "thermodynamics",
    "chemical_kinetics": "kinetics",
    "kinetics": "kinetics",
    "equilibrium": "equilibrium",
    "acids_and_bases": "acids_bases",
    "kinematics": "kinematics",
    "dynamics": "force_energy",
    "mechanics": "force_energy",
    "energy": "force_energy",
    "work": "force_energy",
    "fluids": "fluids",
    "circuits": "circuits",
    "electrostatics": "circuits",
    "magnetism": "circuits",
    # bio / biochem
    "amino_acids": "amino_acids",
    "proteins": "protein_structure",
    "protein_structure": "protein_structure",
    "enzymes": "enzymes",
    "carbohydrates": "carbohydrates",
    "lipids": "lipids",
    "lipid_metabolism": "lipids",
    "dna_and_rna": "nucleic_acids",
    "nucleic_acids": "nucleic_acids",
    "cell_membrane": "cell_biology",
    "biosignaling": "cell_biology",
    "parts_of_cell": "cell_biology",
    "cytoskeleton": "cell_biology",
    "genetics": "gene_regulation",
    # psych / soc
    "sensation_and_perception": "sensation_perception",
    "cognition": "cognition",
    "attention": "cognition",
    "intelligence": "cognition",
    "language": "cognition",
    "memory": "memory",
    "learning": "learning",
    "behavior": "learning",
    "motivation": "motivation_emotion",
    "emotion": "motivation_emotion",
    "emotions": "motivation_emotion",
    "identity": "identity",
    "personality": "identity",
    "biology_and_behavior": "biological_behavior",
    "consciousness": "biological_behavior",
    "social_structure": "social_structure",
    "social_stratification": "social_structure",
    "socialization": "social_structure",
    "social_behavior": "social_thinking",
    "social_interaction": "social_thinking",
    "social_perception": "social_thinking",
    "attitudes": "social_thinking",
    "demographics": "demographics",
}


def _topic_id_from_source_tag(tag: str) -> str | None:
    """Map a recognized deck's own topic tag to an Ante topic id, or None.

    Scans the tag's segments leaf-first, so the most specific recognized topic
    wins (e.g. ``MileDown::Behavioral::Biology_and_Behavior::Brain`` falls back
    from the unmapped ``Brain`` to ``Biology_and_Behavior``)."""
    parts = [p for p in tag.split("::") if p]
    if len(parts) < 2 or parts[0].lower() not in _DECK_ROOTS:
        return None
    for seg in reversed(parts[1:]):
        tid = _DECK_TOPIC.get(seg.lower())
        if tid:
            return tid
    return None


@dataclass(frozen=True)
class TagMatch:
    tag: str
    score: float
    runner_up: float


def _topic_scores(text: str) -> list[tuple[str, str, float]]:
    """(section_id, topic_id, score) for every topic with a positive hit."""
    scores: list[tuple[str, str, float]] = []
    for section_id, topics in _KEYWORDS.items():
        for topic_id, phrases in topics.items():
            s = 0.0
            for phrase, weight in phrases:
                if f" {phrase.strip()} " in text or phrase in text:
                    s += weight
            if s > 0:
                scores.append((section_id, topic_id, s))
    return scores


def match_topic(
    front: str = "",
    back: str = "",
    *,
    deck_name: str = "",
    tags: list[str] | None = None,
    outline: Outline | None = None,
) -> TagMatch | None:
    """Best confident topic for a card, or ``None`` when the evidence is thin
    or ambiguous. Deck name and existing tags are weighted context, not proof.
    """
    outline = outline or load_outline()
    by_id = {t.id: t for t in outline.all_topic_objs()}

    # 1) trust a recognized deck's own topic hierarchy first — deterministic
    #    and far higher signal than card text
    for tag in tags or []:
        tid = _topic_id_from_source_tag(tag)
        if tid and tid in by_id:
            # a direct taxonomy hit is maximally confident
            return TagMatch(tag=by_id[tid].tag, score=99.0, runner_up=0.0)

    # 2) otherwise fall back to conservative keyword matching on the text
    text = _normalize(" ".join([front, back, deck_name, " ".join(tags or [])]))
    scores = _topic_scores(text)
    if not scores:
        return None
    scores.sort(key=lambda x: -x[2])
    section_id, topic_id, top = scores[0]
    runner = scores[1][2] if len(scores) > 1 else 0.0
    if top < MIN_SCORE or (top - runner) < MIN_MARGIN:
        return None
    t = by_id.get(topic_id)
    if t and t.section_id == section_id:
        return TagMatch(tag=t.tag, score=top, runner_up=runner)
    return None
