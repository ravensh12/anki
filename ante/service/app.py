# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""FastAPI app for AI card generation + model eval (PRD 3.2, committed stack).

Run it:

    pip install -r ante/service/requirements.txt
    uvicorn ante.service.app:app --port 8723

Every endpoint is a thin wrapper over the dependency-free ``ante`` logic, so
the science is identical whether called in-process (AI off) or over HTTP. The
desktop app NEVER requires this service to review or produce a score.
"""

from __future__ import annotations

from ante.ai.checker import check_cards
from ante.ai.eval import answer_selection_eval, card_quality_eval, load_gold
from ante.ai.generate import generate_cards, generate_cards_for_topic
from ante.ai.provider import get_provider
from ante.config import CONFIG

try:
    from fastapi import FastAPI
    from pydantic import BaseModel

    _HAVE_FASTAPI = True
except ImportError:  # pragma: no cover - optional dependency
    _HAVE_FASTAPI = False


if _HAVE_FASTAPI:

    class GenerateRequest(BaseModel):
        source: str
        source_id: str = "source"
        topic_tag: str | None = None
        max_cards: int = 10
        offline: bool = False

    class CheckRequest(BaseModel):
        cards: list[dict]
        offline: bool = True

    class EvalRequest(BaseModel):
        source: str | None = None
        source_id: str = "source"
        offline: bool = False


def create_app():
    if not _HAVE_FASTAPI:
        raise RuntimeError(
            "FastAPI not installed. Run: pip install -r "
            "ante/service/requirements.txt"
        )

    app = FastAPI(title="Ante model/AI service", version="1.0")

    @app.get("/health")
    def health() -> dict:
        provider = get_provider(force_offline=False)
        return {
            "status": "ok",
            "provider": provider.name,
            "ai_card_cutoff": CONFIG.ai_card_cutoff,
        }

    @app.post("/generate")
    def generate(req: GenerateRequest) -> dict:
        provider = get_provider(force_offline=req.offline)
        if req.topic_tag:
            result = generate_cards_for_topic(
                req.source,
                req.topic_tag,
                source_id=req.source_id,
                max_cards=req.max_cards,
                provider=provider,
            )
        else:
            result = generate_cards(
                req.source,
                source_id=req.source_id,
                max_cards=req.max_cards,
                provider=provider,
            )
        return result.as_dict()

    @app.post("/check")
    def check(req: CheckRequest) -> dict:
        from ante.ai.provider import GeneratedCard

        gold = load_gold(_default_gold_path())
        cards = [
            GeneratedCard(
                front=c.get("front", ""),
                back=c.get("back", ""),
                source_id=c.get("source_id", "source"),
                source_span=tuple(c.get("source_span", (0, 0))),
                source_quote=c.get("source_quote", ""),
                generator=c.get("generator", "external"),
            )
            for c in req.cards
        ]
        report = check_cards(cards, gold, batch_cutoff=CONFIG.ai_card_cutoff)
        return report.as_dict()

    @app.post("/eval")
    def evaluate(req: EvalRequest) -> dict:
        provider = get_provider(force_offline=req.offline)
        gold = load_gold(_default_gold_path())
        out: dict = {"provider": provider.name}
        out["answer_selection"] = answer_selection_eval(gold, provider)
        if req.source:
            quality, _ = card_quality_eval(
                req.source,
                req.source_id,
                gold,
                provider,
                batch_cutoff=CONFIG.ai_card_cutoff,
            )
            out["card_quality"] = quality
        return out

    return app


def _default_gold_path():
    from pathlib import Path

    import ante

    return Path(ante.__file__).resolve().parent / "data" / "gold_set.json"


# Module-level app for `uvicorn ante.service.app:app` (only when FastAPI is
# installed; importing this module without FastAPI stays safe).
if _HAVE_FASTAPI:
    app = create_app()
