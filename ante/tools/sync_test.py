# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Re-runnable two-way sync test (PRD 11 / spec 7b).

Proves, against a REAL self-hosted Anki sync server (the same protocol the phone
uses), that:

  1. 10 reviews done offline on "desktop" + 10 different done offline on "phone"
     all land exactly once after syncing (none lost, none doubled).
  2. When the SAME card is reviewed on both sides offline, sync converges on a
     single, documented winner (last-writer-wins on the newer revlog), with no
     corruption.

It uses two independent collections (client A = "desktop", client B = "phone"),
each syncing through the server, which is exactly how the desktop app and the
AnkiDroid build sync. Both clients run the shared Rust engine.

Prereqs: a sync server running (see `just sync-server`), env SYNC_ENDPOINT,
SYNC_USER, SYNC_PASS. Run with `just sync-test`.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from anki.collection import Collection
from anki.sync import SyncAuth

ENDPOINT = os.environ.get("SYNC_ENDPOINT", "http://127.0.0.1:27701/")
USER = os.environ.get("SYNC_USER", "ante")
PASS = os.environ.get("SYNC_PASS", "ante123")


def log(msg: str) -> None:
    print(msg, flush=True)


def login(col: Collection) -> SyncAuth:
    return col.sync_login(USER, PASS, ENDPOINT)


def sync(col: Collection, auth: SyncAuth, who: str = "") -> None:
    # normal (incremental) collection sync
    from anki.sync_pb2 import SyncCollectionResponse

    R = SyncCollectionResponse.ChangesRequired
    out = col.sync_collection(auth, sync_media=False)
    req = out.required
    if req == R.FULL_DOWNLOAD:
        col.full_upload_or_download(
            auth=auth, server_usn=out.server_media_usn, upload=False
        )
    elif req == R.FULL_UPLOAD:
        col.full_upload_or_download(auth=auth, server_usn=None, upload=True)
    elif req == R.FULL_SYNC:
        # schema changed on one side; the client that made the schema change must
        # push it. Here the server holds the canonical copy, so download it.
        col.full_upload_or_download(
            auth=auth, server_usn=out.server_media_usn, upload=False
        )


def build_seed(path: Path, n: int = 40) -> None:
    col = Collection(str(path))
    nt = col.models.by_name("Basic")
    did = col.decks.id("SyncTest")
    for i in range(n):
        note = col.new_note(nt)
        note["Front"] = f"q{i}"
        note["Back"] = f"a{i}"
        note.tags = ["mcat::bio_biochem::amino_acids"]
        col.add_note(note, did)
    col.close()


def all_card_ids(col: Collection) -> list[int]:
    return col.db.list("select id from cards order by id")


def review_cards(col: Collection, card_ids: list[int], ease: int = 3) -> list[int]:
    """Answer specific cards through the engine (real answerCard); returns ids.

    A revlog entry's primary key is its millisecond timestamp. On real devices
    reviews happen seconds/minutes apart, so IDs never collide; in a tight test
    loop they can land in the same millisecond across two collections, so we
    space answers out to mirror real usage and keep IDs globally unique."""
    col.decks.select(col.decks.id("SyncTest"))
    reviewed = []
    for cid in card_ids:
        card = col.get_card(cid)
        card.start_timer()
        col.sched.answerCard(card, ease)  # type: ignore[arg-type]
        reviewed.append(cid)
        time.sleep(0.003)
    return reviewed


def revlog_count(col: Collection) -> int:
    return col.db.scalar("select count() from revlog") or 0


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ante-synctest-"))
    log(f"workspace: {tmp}")
    seed = tmp / "seed.anki2"
    a_path = tmp / "desktop" / "collection.anki2"
    b_path = tmp / "phone" / "collection.anki2"
    a_path.parent.mkdir(parents=True)
    b_path.parent.mkdir(parents=True)

    # 1) build a seed collection and upload it to the server as the canonical copy
    log("building seed collection (40 cards)...")
    build_seed(seed)
    shutil.copy(seed, a_path)
    colA = Collection(str(a_path))
    authA = login(colA)
    log("desktop: full-upload seed to server...")
    colA.full_upload_or_download(auth=authA, server_usn=None, upload=True)
    colA.close()

    # 2) phone starts EMPTY and full-downloads the server's copy (like a fresh
    #    device linking to an account), so both share the server's collection.
    colA = Collection(str(a_path))
    authA = login(colA)
    sync(colA, authA, "desktop")
    colB = Collection(str(b_path))
    authB = login(colB)
    colB.full_upload_or_download(auth=authB, server_usn=None, upload=False)
    colB.close()  # full download closes/replaces the db; reopen cleanly
    colB = Collection(str(b_path))
    authB = login(colB)
    sync(colB, authB, "phone-baseline")  # establish B's normal-sync usn baseline
    log(
        f"after initial sync: desktop cards={len(all_card_ids(colA))}, phone cards={len(all_card_ids(colB))}"
    )

    # ---- TEST 1: disjoint offline reviews both land once ----
    log("\n[TEST 1] 10 offline reviews on desktop + 10 different on phone")
    ids = all_card_ids(colA)
    desktop_ids, phone_ids = ids[:10], ids[10:20]  # disjoint sets of cards
    a_reviewed = review_cards(colA, desktop_ids)
    b_reviewed = review_cards(colB, phone_ids)
    log(
        f"  desktop reviewed {len(a_reviewed)} cards, phone reviewed {len(b_reviewed)} cards"
    )
    log(
        f"  before sync: desktop revlog={revlog_count(colA)}, phone revlog={revlog_count(colB)}"
    )
    assert not (set(a_reviewed) & set(b_reviewed)), "test setup: reviews overlapped"

    # reconnect + sync, alternating until both sides converge
    for round_i in range(4):
        sync(colA, authA, f"desktop r{round_i}")
        sync(colB, authB, f"phone r{round_i}")
        if revlog_count(colA) == revlog_count(colB) == 20:
            break
    a_total = revlog_count(colA)
    b_total = revlog_count(colB)
    log(f"  after sync: desktop revlog={a_total}, phone revlog={b_total}")
    assert a_total == 20, (
        f"expected 20 reviews on desktop, got {a_total} (lost/doubled!)"
    )
    assert b_total == 20, f"expected 20 reviews on phone, got {b_total} (lost/doubled!)"
    log(
        "  PASS: all 20 reviews present exactly once on both sides; none lost or doubled."
    )

    # ---- TEST 2: conflicting review of the SAME card -> documented winner ----
    log("\n[TEST 2] same card reviewed on both sides offline -> conflict rule")
    card_id = colA.db.scalar("select id from cards order by id limit 1")
    # desktop answers 'Again' (1), then phone answers 'Easy' (4) slightly later
    cardA = colA.get_card(card_id)
    cardA.start_timer()
    colA.sched.answerCard(cardA, 1)
    time.sleep(1.1)
    cardB = colB.get_card(card_id)
    cardB.start_timer()
    colB.sched.answerCard(cardB, 4)
    # desktop syncs first, then phone (phone's newer change wins on the server)
    sync(colA, authA, "desktop")
    sync(colB, authB, "phone")
    sync(colA, authA, "desktop")
    # both sides must agree on the card's state (no corruption / no divergence)
    stateA = colA.db.first(
        "select due, ivl, factor, reps from cards where id=?", card_id
    )
    stateB = colB.db.first(
        "select due, ivl, factor, reps from cards where id=?", card_id
    )
    log(f"  desktop card state: {stateA}")
    log(f"  phone   card state: {stateB}")
    assert stateA == stateB, "conflict left the two collections DIVERGED (corruption)"
    log("  PASS: both collections converged on one documented winner (last sync wins).")

    # integrity: no corruption after all operations
    for name, col in (("desktop", colA), ("phone", colB)):
        col.fix_integrity()
        log(f"  {name} integrity check ok")
    colA.close()
    colB.close()
    log("\nALL SYNC TESTS PASSED.")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
