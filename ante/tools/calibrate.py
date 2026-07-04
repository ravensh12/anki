# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Produce a calibration report + reliability chart for the memory model.

Two sources are supported:

1. ``--predictions file.json`` - a list of {"prob": p, "outcome": 0|1} pairs
   (e.g. held-out FSRS predictions vs actual review results). This drives our own
   Brier / log-loss / ECE numbers and the reliability SVG.

2. ``--collection deck.anki2`` - additionally prints FSRS's own held-out
   evaluation (log loss + RMSE calibration bins) via the backend, which is the
   rigorous number to cite for memory calibration.

Outputs a JSON report and an SVG reliability diagram.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ante.memory import calibrate, render_reliability_svg


def _from_predictions(path: Path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    probs = [float(r["prob"]) for r in raw]
    outcomes = [int(r["outcome"]) for r in raw]
    return probs, outcomes


def _fsrs_eval(collection_path: str) -> dict | None:
    """Best-effort: ask the backend for FSRS's held-out log loss + RMSE."""
    try:
        from anki.collection import Collection

        col = Collection(collection_path)
        try:
            resp = col._backend.evaluate_params(
                search="", ignore_revlogs_before_ms=0, num_of_relearning_steps=1
            )
            return {"fsrs_log_loss": resp.log_loss, "fsrs_rmse_bins": resp.rmse_bins}
        finally:
            col.close()
    except Exception as exc:  # pragma: no cover - depends on review history
        return {"error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Memory calibration report.")
    parser.add_argument("--predictions", type=Path, help="JSON of {prob,outcome}")
    parser.add_argument("--collection", help="optional .anki2 for FSRS eval")
    parser.add_argument("--out-json", type=Path, default=Path("out/calibration.json"))
    parser.add_argument("--out-svg", type=Path, default=Path("out/calibration.svg"))
    args = parser.parse_args()

    report_dict: dict = {}
    if args.predictions:
        probs, outcomes = _from_predictions(args.predictions)
        report = calibrate(probs, outcomes)
        report_dict["our_calibration"] = report.as_dict()
        args.out_svg.parent.mkdir(parents=True, exist_ok=True)
        args.out_svg.write_text(render_reliability_svg(report), encoding="utf-8")
        print(
            f"n={report.n}  brier={report.brier:.4f}  log_loss={report.log_loss:.4f}  "
            f"ece={report.ece:.4f}  observed_recall={report.observed_recall:.3f} "
            f"CI={report.recall_ci[0]:.3f}-{report.recall_ci[1]:.3f}"
        )
        print(f"reliability chart -> {args.out_svg}")

    if args.collection:
        report_dict["fsrs_eval"] = _fsrs_eval(args.collection)
        print(f"fsrs_eval: {report_dict['fsrs_eval']}")

    if not report_dict:
        parser.error("provide --predictions and/or --collection")

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report_dict, indent=2), encoding="utf-8")
    print(f"report -> {args.out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
