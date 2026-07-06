# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Generate the den's cinematic asset library with Higgsfield (+ ElevenLabs).

Everything the den shows is generated ONCE by this tool, hand-curated, and
checked into ``ante/web/assets`` — a bounded, pre-built library, not live
generation. Card text is never baked into any of it: these are backdrops and
portraits; the cards themselves stay crisp HTML composited on top.

The library (see also qt/aqt/ante.py::world_assets_present):

  * ``den_{dawn,day,dusk,night}.jpg``  — the Emerald Room by hour (Soul)
  * ``den_{dawn,day,dusk,night}.mp4``  — living loops of the same plates (DoP),
    each with a ``.webm`` (VP9) twin written automatically: Anki's
    QtWebEngine ships no H.264, so the den only plays the webm
  * ``dealer.jpg`` / ``dealer_idle.mp4`` — Sahir at the table (Soul + DoP)
  * ``city_*.jpg`` + ``final_table.jpg`` — one signature plate per Circuit stop
  * ``avatar_1..6.jpg``                — seat portraits for the avatar picker
  * ``vo_{seat,morning,midnight,call_*}.mp3`` + ``vo_film_1..8.mp3`` — dealer
    voice lines (ElevenLabs preferred; OpenAI TTS fallback, transcoded to
    mp3). One actor per render: the speech cache is keyed on engine+voice,
    so a full ``--voice`` pass always recasts every line together

Camera discipline: every DoP job is pinned to a single curated camera-motion
preset (``HF_DOP_MOTION`` in ante/ai/studio.py) and MOTION_RULES bans film
rigs in frame and rain indoors — free-roaming AI cameras once drifted a rig
through the felt, and one take rained inside the room. Curate hard: reroll
any drifted shot with ``--take N`` and keep only clean frames.

Character consistency: the repo's Soul integration is prompt-only, so Sahir is
locked by ``SAHIR`` (one fixed, detailed character prompt reused verbatim in
every scene) and by curation — regenerate any shot that drifts and keep the
takes that match. Same idea for the room (``ROOM``).

Auth (from cloud.higgsfield.ai / elevenlabs.io):
    export HF_KEY="ID:SECRET"            # or HF_API_KEY + HF_API_SECRET
    export ELEVENLABS_API_KEY=...        # only for --voice

Usage:
    PYTHONPATH=. out/pyenv/bin/python -m ante.tools.gen_world --scene all
    PYTHONPATH=. out/pyenv/bin/python -m ante.tools.gen_world --scene den_night --motion
    PYTHONPATH=. out/pyenv/bin/python -m ante.tools.gen_world --voice
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from ante.ai.studio import Studio, elevenlabs_available, higgsfield_available

ASSETS = Path(__file__).resolve().parent.parent / "web" / "assets"
# generation cache + budget ledger live OUTSIDE the bundled assets (out/ is
# git-ignored and never packaged); only curated takes are copied into ASSETS
CACHE = Path(__file__).resolve().parents[2] / "out" / "ante_world_studio"

# ---- the fixed character + room prompts (consistency = reuse verbatim) ---- #

SAHIR = (
    "Sahir, an ageless djinn card dealer: a tall elegant man of indeterminate "
    "age in a charcoal three-piece suit with a deep green silk tie, faint "
    "silver at the temples, kind exacting amber eyes, thin curls of pale smoke "
    "rising from his shirt cuffs"
)

ROOM = (
    "the Emerald Room, a members-only card den above Canal Street in "
    "Manhattan: one green-felt card table under a low brass lamp, dark wood "
    "panelling, leather chairs, faint cigar haze, floor-to-ceiling windows "
    "with rain streaks and the city's neon bokeh far below"
)

STYLE = (
    "Cinematic photorealistic still, film-noir warmth, anamorphic shallow "
    "depth of field, rich blacks, consistent color grade, no text, no "
    "lettering, no watermark, rain only outside the window glass, the "
    "interior perfectly dry"
)

# Appended to every DoP motion prompt. Two curation bugs shipped once and are
# banned forever here: a film rig drifting into (and through) the card table,
# and rain falling inside the room. The camera is additionally pinned by the
# HF_DOP_MOTION preset in ante/ai/studio.py — prompt and preset together.
MOTION_RULES = (
    "locked-off tripod camera, no camera movement, no dolly, no zoom, "
    "no film equipment or camera rig in frame, rain stays outside the "
    "window glass, the interior stays dry"
)

# scene id -> (output file, prompt, motion prompt for the optional DoP loop)
SCENES: dict[str, tuple[str, str, str]] = {
    "den_dawn": (
        "den_dawn.jpg",
        f"Wide shot of {ROOM}, at dawn: pale gold first light through the rain, "
        f"the lamp still on, the felt freshly brushed, two neat stacks of chips. {STYLE}",
        "rain sliding down glass, dawn light slowly warming, lamp flicker",
    ),
    "den_day": (
        "den_day.jpg",
        f"Wide shot of {ROOM}, midday: soft grey daylight, the city awake below, "
        f"cards squared in the shoe, quiet and ready. {STYLE}",
        "clouds drifting past the windows, dust motes in the light",
    ),
    "den_dusk": (
        "den_dusk.jpg",
        f"Wide shot of {ROOM}, at dusk: amber sunset burning through the rain, "
        f"neon starting to bloom below, long shadows across the felt. {STYLE}",
        "sunset fading to neon, rain intensifying softly",
    ),
    "den_night": (
        "den_night.jpg",
        f"Wide shot of {ROOM}, deep night: the room lit only by the brass lamp, "
        f"neon rain outside, smoke curling through the cone of light. {STYLE}",
        "smoke curling through lamplight, neon pulsing in the rain",
    ),
    "dealer": (
        "dealer.jpg",
        f"Portrait of {SAHIR}, seated at the table in {ROOM} at night, hands "
        f"folded on the felt, looking directly at the viewer. {STYLE}",
        "smoke curls from his cuffs, a slow patient blink, lamplight breathing",
    ),
    "sahir_deal": (
        "sahir_deal.jpg",
        f"Medium shot of {SAHIR} mid-deal at the table in {ROOM} at night, one "
        f"hand releasing a playing card that glides across the green felt, "
        f"three cards already fanned face-down before him, motion and intent "
        f"in his posture. {STYLE}",
        "he deals cards smoothly across the felt one after another, smoke "
        "drifting, lamplight steady",
    ),
    "felt_close": (
        "felt_close.jpg",
        f"Extreme close-up across the green felt of the table in {ROOM}: two "
        f"face-down playing cards and a short stack of clay chips in the brass "
        f"lamplight, shallow focus, cigar smoke drifting low. {STYLE}",
        "a dealt card slides in and settles, chip stack shivers, smoke rolls low",
    ),
    "city_new_york": (
        "city_new_york.jpg",
        f"Establishing shot for the Circuit's first stop: {ROOM} seen from "
        f"outside the rain-streaked window at night, the lamp a warm island. {STYLE}",
        "rain, neon pulse, the lamp flickering warm",
    ),
    "city_monte_carlo": (
        "city_monte_carlo.jpg",
        "Establishing shot for the Circuit: Salon Bleu, a private card salon on "
        "a Monte Carlo terrace at dusk, one green-felt table, sea air moving "
        f"white curtains, the Mediterranean going dark beyond the balustrade. {STYLE}",
        "curtains breathing in sea air, dusk deepening",
    ),
    "city_havana": (
        "city_havana.jpg",
        "Establishing shot for the Circuit: Casa Verde, a courtyard card room "
        "in Havana at night, one green-felt table under strung filament bulbs, "
        f"palms and peeling pastel walls, warm humid air. {STYLE}",
        "filament bulbs swaying gently, palm shadows moving",
    ),
    "city_macau": (
        "city_macau.jpg",
        "Establishing shot for the Circuit: the Jade House, a high-rise card "
        "room in Macau at night, one green-felt table before a wall of glass, "
        f"the neon harbor far below, jade and gold accents. {STYLE}",
        "harbor neon shimmering, slow drift of clouds past the tower",
    ),
    "final_table": (
        "final_table.jpg",
        "The Final Table: a single green-felt card table on a Manhattan "
        "rooftop at first light, the storm cleared, the city gold and quiet "
        f"below, one chair pulled out. {STYLE}",
        "sunrise warming, a light wind on the felt",
    ),
    # seat portraits — six distinct players, same room, same grade
    "avatar_1": (
        "avatar_1.jpg",
        f"Portrait of a young woman with braided dark hair in a sharp blazer, "
        f"seated at the table in {ROOM}, calm and ready, looking at the viewer. {STYLE}",
        "",
    ),
    "avatar_2": (
        "avatar_2.jpg",
        f"Portrait of a young man with glasses and rolled shirtsleeves, seated "
        f"at the table in {ROOM}, quietly confident, looking at the viewer. {STYLE}",
        "",
    ),
    "avatar_3": (
        "avatar_3.jpg",
        f"Portrait of a woman with silver-streaked hair and a velvet jacket, "
        f"seated at the table in {ROOM}, amused and unhurried, looking at the viewer. {STYLE}",
        "",
    ),
    "avatar_4": (
        "avatar_4.jpg",
        f"Portrait of a broad-shouldered man with a beard and an open collar, "
        f"seated at the table in {ROOM}, steady, looking at the viewer. {STYLE}",
        "",
    ),
    "avatar_5": (
        "avatar_5.jpg",
        f"Portrait of a young woman in a hijab and a tailored coat, seated at "
        f"the table in {ROOM}, sharp-eyed and composed, looking at the viewer. {STYLE}",
        "",
    ),
    "avatar_6": (
        "avatar_6.jpg",
        f"Portrait of an older man with a shaved head and a houndstooth "
        f"waistcoat, seated at the table in {ROOM}, warm and shrewd, looking at "
        f"the viewer. {STYLE}",
        "",
    ),
}

# scene id -> which motion output it produces (only the living plates loop)
MOTION_OUT = {
    "den_dawn": "den_dawn.mp4",
    "den_day": "den_day.mp4",
    "den_dusk": "den_dusk.mp4",
    "den_night": "den_night.mp4",
    "dealer": "dealer_idle.mp4",
    "sahir_deal": "sahir_deal.mp4",
    "felt_close": "felt_close.mp4",
}

# dealer voice lines (ElevenLabs, persona "dealer") — played softly in-app,
# never required, never numeric (evergreen, like the OS notification copy)
VOICE_LINES = {
    "vo_seat.mp3": "Your seat's been waiting. Let's see what you remember.",
    "vo_morning.mp3": "The morning game opens. Sit down cold — it counts double.",
    "vo_midnight.mp3": "Last hand before lights out. Play it slow.",
    # The Call — Sahir rings you for a three-card hand
    "vo_call_open.mp3": "Don't hang up. Three cards, right now, while they're warm.",
    "vo_call_done.mp3": (
        "That's the hand. Go on with your day — I'll hold the table."
    ),
    # the cold open — one line per shot: the hook (1–3), the walkthrough
    # (4–7: play a hand, follow the data, the quiz-only mastery gate, the
    # published receipts), then the logo close (8).
    "vo_film_1.mp3": (
        "You're pre-med. Motivation was never your problem — "
        "nobody chooses this path without plenty of it."
    ),
    "vo_film_2.mp3": (
        "But motivation on its own becomes cramming, all-nighters, and "
        "lopsided weeks. The exact pattern the science calls the worst "
        "way to learn."
    ),
    "vo_film_3.mp3": (
        "So this is Ante: an MCAT trainer where I run your schedule. "
        "Twice a day a reminder finds you, you sit down, and I deal you "
        "exactly the cards you're about to forget."
    ),
    "vo_film_4.mp3": (
        "Here's the whole game. A card comes up. Before it turns you tell "
        "me where you stand — check, call, or raise. Turn it over. Then "
        "grade it honestly: again, hard, good, or easy. That's one hand."
    ),
    "vo_film_5.mp3": (
        "Your answer doesn't vanish. The grade, the raise, even your "
        "hesitation feed the engine, and it fits your personal forgetting "
        "curve. Then it deals that card back on the exact night you'd start "
        "to lose it — three days, eight, twenty-one. That's why the cards "
        "repeat."
    ),
    "vo_film_6.mp3": (
        "And hear this: rating your own flashcards never wins a table. "
        "Cards only decide when you review. The Circuit moves on proof — "
        "quiz questions and open answers, graded cold. Cross the bar on "
        "application, and the table is yours."
    ),
    "vo_film_7.mp3": (
        "Don't take my word for it. Tested beats re-read, sixty-one to "
        "forty, one week out. Spacing beats cramming across eight hundred "
        "thirty-nine assessments. And of every study technique measured, "
        "two rate high. You're looking at both."
    ),
    "vo_film_8.mp3": "Ante. The MCAT, played right. Take your seat.",
}


def _studio() -> Studio:
    # content-addressed cache: re-runs are free and only new prompts spend
    return Studio(CACHE)


def _copy(src: Path, dest: Path) -> None:
    dest.write_bytes(src.read_bytes())
    print(f"  wrote {dest.relative_to(ASSETS.parent.parent)}")


def _webm_twin(mp4: Path) -> None:
    """Transcode a curated mp4 loop to VP9 next to it. Anki's QtWebEngine
    ships without proprietary codecs — H.264 decodes to a black rectangle —
    so the den only plays a loop when its .webm twin exists."""
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        print("  ffmpeg not found — skipped the .webm twin (the den needs it)")
        return
    out = mp4.with_suffix(".webm")
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-i", str(mp4),
         "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "33", "-row-mt", "1",
         "-pix_fmt", "yuv420p", "-an", str(out)],
        check=True,
    )
    print(f"  wrote {out.relative_to(ASSETS.parent.parent)}")


def gen_scene(studio: Studio, scene: str, motion: bool, take: int = 0) -> None:
    filename, prompt, motion_prompt = SCENES[scene]
    print(f"[{scene}] rendering still (take {take}) …", flush=True)
    # ``take`` is part of the content-addressed spec: bump it to reroll a shot
    # that drifted off-model, without invalidating the takes you kept.
    spec = {"prompt": prompt, "title": scene, "caption": "", "take": take}
    cached = studio.lookup("still", spec)
    if cached and cached.provider == "offline-engraver" and higgsfield_available():
        # a keyless/failed run poisoned this spec — drop it and re-render live
        studio.forget("still", spec)
    still = studio.still(spec)
    if still.provider == "offline-engraver":
        print(
            f"[{scene}] Higgsfield unavailable or the call failed "
            "(check key/credits) — offline plate produced (not copied)"
        )
        return
    _copy(studio.path_of(still), ASSETS / filename)
    if motion and motion_prompt and scene in MOTION_OUT:
        print(f"[{scene}] animating loop …", flush=True)
        clip = studio.motion({"motion": f"{motion_prompt}. {MOTION_RULES}"}, still)
        if clip:
            dest = ASSETS / MOTION_OUT[scene]
            _copy(studio.path_of(clip), dest)
            _webm_twin(dest)
        else:
            print(f"[{scene}] motion unavailable (budget/keys) — still only")


def gen_voice(studio: Studio) -> None:
    for filename, line in VOICE_LINES.items():
        print(f"[voice] {filename} …", flush=True)
        ref = studio.speech(line, persona="dealer")
        if ref is None:
            print("  no ELEVENLABS_API_KEY / OPENAI_API_KEY — skipped")
            continue
        src = studio.path_of(ref)
        out = ASSETS / filename
        if ref.filename.endswith(".wav") and filename.endswith(".mp3"):
            # the OpenAI fallback emits WAV; the den looks the mp3 name up in
            # world_assets, so transcode when ffmpeg is around (else ship WAV)
            mp3 = _mp3_from_wav(src)
            if mp3 is None:
                out = out.with_suffix(".wav")
            else:
                src = mp3
        _copy(src, out)


def _mp3_from_wav(wav: Path) -> Path | None:
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        return None
    out = wav.with_suffix(".mp3")
    if not out.is_file():
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav),
             "-codec:a", "libmp3lame", "-qscale:a", "3", str(out)],
            check=True,
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the den's asset library.")
    ap.add_argument(
        "--scene", default="all", help="scene id or 'all': " + ", ".join(SCENES)
    )
    ap.add_argument(
        "--motion", action="store_true", help="also render DoP living loops"
    )
    ap.add_argument(
        "--voice", action="store_true", help="render the dealer's voice lines"
    )
    ap.add_argument(
        "--take", type=int, default=0, help="reroll counter for off-model shots"
    )
    args = ap.parse_args()

    ASSETS.mkdir(parents=True, exist_ok=True)
    studio = _studio()

    if args.voice:
        if not (elevenlabs_available() or os.environ.get("OPENAI_API_KEY")):
            raise SystemExit("Set ELEVENLABS_API_KEY (or OPENAI_API_KEY) first.")
        gen_voice(studio)
        if args.scene == "all" and not higgsfield_available():
            return

    if not higgsfield_available():
        raise SystemExit(
            "Set HF_KEY (or HF_API_KEY + HF_API_SECRET) first — the den falls "
            "back to its built-in scene until the plates exist."
        )

    scenes = list(SCENES) if args.scene == "all" else [args.scene]
    for s in scenes:
        if s not in SCENES:
            raise SystemExit(f"unknown scene '{s}'. Options: {', '.join(SCENES)}")
        gen_scene(studio, s, motion=args.motion, take=args.take)
    print("\nDone. Curate the takes: rerun any drifted scene with --take N+1.")


if __name__ == "__main__":
    main()
