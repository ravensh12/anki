# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for retrieval over chunked sources + retrieval-augmented generation."""

from ante.ai.generate import generate_cards_for_topic
from ante.ai.provider import OfflineProvider
from ante.ai.retrieval import ChunkRetriever, chunk_text, topic_query

SOURCE = (
    "Amino acids are the building blocks of proteins. Glycine is the only "
    "achiral amino acid. \n\n"
    "Enzymes are biological catalysts. A competitive inhibitor raises the "
    "apparent Km while leaving Vmax unchanged. \n\n"
    "Glycolysis nets two ATP per glucose and occurs in the cytoplasm. "
    "Phosphofructokinase-1 catalyzes the rate-limiting step."
)


def test_chunking_preserves_spans():
    chunks = chunk_text(SOURCE, target_chars=120)
    assert len(chunks) >= 2
    for c in chunks:
        # the recorded span maps back to (roughly) the chunk text
        assert SOURCE[c.start : c.end].strip().startswith(c.text[:10])


def test_retrieval_finds_topic_relevant_chunk():
    r = ChunkRetriever(SOURCE, target_chars=120)
    top = r.retrieve(topic_query("mcat::bio_biochem::enzymes"), k=1)
    assert top
    assert (
        "inhibitor" in top[0].chunk.text.lower()
        or "enzyme" in top[0].chunk.text.lower()
    )


def test_topic_query_strips_prefix():
    # prefix dropped; '::' and '_' become spaces so the query spreads into words
    assert topic_query("mcat::bio_biochem::amino_acids") == "bio biochem amino acids"


def test_rag_generation_carries_rebased_provenance():
    result = generate_cards_for_topic(
        SOURCE,
        "mcat::bio_biochem::glycolysis",
        source_id="bio_ch1",
        max_cards=5,
        provider=OfflineProvider(),
    )
    assert result.cards
    for card in result.cards:
        lo, hi = card.source_span
        # spans are rebased onto the FULL source
        assert 0 <= lo <= hi <= len(SOURCE)
        assert card.source_id == "bio_ch1"


def test_keyword_baseline_available_for_comparison():
    r = ChunkRetriever(SOURCE, target_chars=120)
    kb = r.retrieve_keyword_baseline(topic_query("mcat::bio_biochem::glycolysis"))
    assert kb is not None


def test_service_module_imports_without_fastapi():
    # The core must never hard-depend on FastAPI; importing the service module
    # is safe even when FastAPI is absent.
    import ante.service.app as svc

    assert hasattr(svc, "create_app")
