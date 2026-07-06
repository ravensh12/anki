// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

// Smoke tests for the Ante den (ante/web/den.html served at /_anki/ante).
//
// The harness's Chromium has no Qt pycmd bridge, so bridge writes are no-ops
// here (they are covered by qt/tests/test_ante_payloads.py); these tests
// exercise the real HTTP surface: boot, the login gate, dealing a real card
// from the engine, keyboard play, and the honest error state.

import { expect, test } from "./fixtures";

/** Encode a protobuf generic.String (field 1, length-delimited). */
function protoString(s: string): Buffer {
    const utf8 = Buffer.from(s, "utf-8");
    const lenBytes: number[] = [];
    let len = utf8.length;
    while (len > 127) {
        lenBytes.push((len & 0x7f) | 0x80);
        len >>= 7;
    }
    lenBytes.push(len);
    return Buffer.concat([Buffer.from([0x0a, ...lenBytes]), utf8]);
}

const SEED_TOPIC = "mcat::bio_biochem::enzymes";
const SEED_FRONT = "What does a competitive inhibitor do to Km?";
const SEED_BACK = "Km increases; Vmax is unchanged.";

test("the den boots to the door", async ({ page }) => {
    await page.goto("/_anki/ante");
    // boot fetches /_anki/anteData, sees signed-out, and renders the gate
    await expect(page.locator("#gEmail")).toBeVisible();
    await expect(page.getByText("A members-only card den")).toBeVisible();
});

test("anteData reports the signed-out state", async ({ page }) => {
    const response = await page.request.get("/_anki/anteData?budget=75");
    expect(response.status()).toBe(200);
    const data = await response.json();
    expect(data.signed_out).toBe(true);
    expect(data.auth.signed_in).toBe(false);
});

test("a table session deals a real card and plays from the keyboard", async ({ page }) => {
    // seed one MCAT-tagged note through the JSON import endpoint
    const seed = await page.request.post("/_anki/importJsonString", {
        headers: { "Content-type": "application/binary" },
        data: protoString(JSON.stringify({
            default_deck: "MCAT",
            default_notetype: "Basic",
            notes: [{
                fields: [SEED_FRONT, SEED_BACK],
                tags: [SEED_TOPIC],
                notetype: "Basic",
                deck: "MCAT",
            }],
        })),
    });
    expect(seed.status()).toBe(200);

    await page.goto("/_anki/ante");
    await expect(page.locator("#gEmail")).toBeVisible();

    // open the Table directly (sign-in writes go over the Qt bridge, which
    // does not exist in this browser)
    await page.evaluate((topic) => {
        (window as any).openTable(topic);
    }, SEED_TOPIC);
    const sheet = page.locator("#ov .sheet");
    await expect(sheet).toBeVisible();
    await expect(sheet.getByRole("button", { name: /Deal me in/ })).toBeVisible();

    // the overlay is an accessible dialog
    await expect(sheet).toHaveAttribute("role", "dialog");
    await expect(sheet).toHaveAttribute("aria-modal", "true");

    // Enter = "Deal me in" — the engine deals a real card off the shoe (the
    // premade MCAT deck self-seeds, so the exact card is the engine's pick)
    await page.keyboard.press("Enter");
    await expect(sheet.locator(".playcard .q")).toBeVisible();
    await expect(sheet.getByRole("button", { name: /Raise/ })).toBeVisible();

    // 3 = Raise (pre-flip confidence)
    await page.keyboard.press("3");
    await expect(sheet.getByRole("button", { name: /Turn it over/ })).toBeVisible();

    // Space = turn the card
    await page.keyboard.press(" ");
    await expect(sheet.locator(".playcard .a")).toBeVisible();
    await expect(sheet.locator(".easeRow")).toBeVisible();

    // 3 = Good; the local tally advances (the actual grade write is
    // bridge-only and covered by the qt tests)
    await page.keyboard.press("3");
    await expect(sheet.getByText("1 played")).toBeVisible();

    // Escape leaves the room
    await page.keyboard.press("Escape");
    await expect(page.locator("#ov .sheet")).toHaveCount(0);

    // leaving never restarts progress: sitting back down the same night, the
    // tally picks up where it left off instead of reading 0 played
    await page.evaluate((topic) => {
        (window as any).openTable(topic);
    }, SEED_TOPIC);
    const again = page.locator("#ov .sheet");
    await expect(again.getByRole("button", { name: /Deal me in/ })).toBeVisible();
    await expect(again.getByText("1 played")).toBeVisible();
});

test("the practice quiz resumes at the question it left", async ({ page }) => {
    await page.goto("/_anki/ante");
    await expect(page.locator("#gEmail")).toBeVisible();

    await page.evaluate(() => {
        (window as any).openPracticeQuiz();
    });
    const sheet = page.locator("#ov .sheet");
    await expect(sheet.getByText("Q 1 / 6")).toBeVisible();

    // answer the first question from the keys (1 = first choice), then walk
    // out mid-quiz
    await expect(sheet.locator(".choice").first()).toBeVisible();
    await page.keyboard.press("1");
    await expect(sheet.getByRole("button", { name: /Next/ })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator("#ov .sheet")).toHaveCount(0);

    // reopening resumes at question 2, not question 1
    await page.evaluate(() => {
        (window as any).openPracticeQuiz();
    });
    await expect(page.getByText(/resuming at question 2 of 6/)).toBeVisible();
    await expect(page.locator("#ov .sheet").getByText("Q 2 / 6")).toBeVisible();
});

test("a full-length keeps its answers when you leave", async ({ page }) => {
    await page.goto("/_anki/ante");
    await expect(page.locator("#gEmail")).toBeVisible();

    await page.evaluate(() => {
        (window as any).openFullLength(1);
    });
    const sheet = page.locator("#ov .sheet");
    await expect(sheet.getByText(/0\/\d+ answered/)).toBeVisible();
    await expect(sheet.locator(".flq .choice").first()).toBeVisible();

    // answer one question in the open section (driven directly — the harness
    // renders the signed-out gate above the overlay, which eats clicks), then
    // leave without submitting
    await page.evaluate("flPick(S.fl.sections[0].items[0].id, 1)");
    await expect(sheet.getByText(/1\/\d+ answered/)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator("#ov .sheet")).toHaveCount(0);

    // the sitting resumes with the answer intact
    await page.evaluate(() => {
        (window as any).openFullLength(1);
    });
    await expect(page.getByText(/Resuming full-length 1/)).toBeVisible();
    await expect(page.locator("#ov .sheet").getByText(/1\/\d+ answered/)).toBeVisible();
});

test("the cold open walks through a hand, the engine, the gate, and the proof", async ({ page }) => {
    await page.goto("/_anki/ante");
    await expect(page.locator("#gEmail")).toBeVisible();

    // roll the projector, then step it shot by shot — filmJump kills the
    // shot timers so assertions never race the auto-advance
    await page.evaluate(() => (window as any).filmStart(false));
    await expect(page.locator(".film")).toBeVisible();

    // shot 4: a real hand plays itself under a ghost cursor
    await page.evaluate(() => (window as any).filmJump("hand"));
    await expect(page.locator("#ghostCur")).toBeVisible();
    await expect(page.locator(".demohand .playcard")).toContainText("competitive inhibitor");
    await expect(page.locator(".dh-conf .isPressed")).toContainText("Raise");
    await expect(page.locator(".dh-ease .isPressed")).toContainText("Good");
    await expect(page.locator(".dh-out")).toContainText("next deal in 3 days");

    // shot 5: the answer flows into FSRS and back out as the re-deal schedule
    await page.evaluate(() => (window as any).filmJump("engine"));
    await expect(page.locator("#fcurve")).toBeVisible();
    await expect(page.locator(".engpipe")).toContainText("FSRS");
    await expect(page.locator(".engpipe")).toContainText("3d · 8d · 21d");
    await expect(page.locator(".film .caption")).toContainText("Every answer becomes your schedule");

    // shot 6: only application is shown to the mastery gate
    await page.evaluate(() => (window as any).filmJump("gate"));
    await expect(page.locator(".gtile")).toContainText("Enzyme Kinetics");
    await expect(page.locator(".gnope")).toContainText("never counted");
    await expect(page.locator(".gscale")).toContainText("80%");
    await expect(page.locator(".gwon")).toContainText("Won on application");

    // shot 7: the receipts carry the published numbers
    await page.evaluate(() => (window as any).filmJump("proof"));
    await expect(page.locator(".pf1")).toContainText("61%");
    await expect(page.locator(".pf1")).toContainText("40%");
    await expect(page.locator(".pf2")).toContainText("839 assessments");
    await expect(page.locator(".pf3")).toContainText("Dunlosky");

    // the door out still works and lands back at the gate
    await page.evaluate(() => (window as any).filmEnd());
    await expect(page.locator(".film")).toHaveCount(0);
    await expect(page.locator("#gEmail")).toBeVisible();
});

test("an unreachable engine shows the honest error state, not a win", async ({ page }) => {
    await page.goto("/_anki/ante");
    await expect(page.locator("#gEmail")).toBeVisible();

    await page.evaluate((topic) => {
        (window as any).openTable(topic);
    }, SEED_TOPIC);
    const sheet = page.locator("#ov .sheet");
    await expect(sheet.getByRole("button", { name: /Deal me in/ })).toBeVisible();

    // cut the line mid-session
    await page.route("**/_anki/anteStudy*", (route) => route.abort());
    await page.route("**/_anki/anteQuiz*", (route) => route.abort());
    await page.keyboard.press("Enter");

    await expect(sheet.getByText("Could not reach the engine.")).toBeVisible();
    await expect(sheet.getByRole("button", { name: /Retry the deal/ })).toBeVisible();
    // never a fake victory
    await expect(sheet.getByText("Clean sweep")).toHaveCount(0);

    // the line comes back; Enter retries the deal and a card appears
    await page.unroute("**/_anki/anteStudy*");
    await page.unroute("**/_anki/anteQuiz*");
    await page.keyboard.press("Enter");
    await expect(sheet.locator(".playcard .q")).toBeVisible();
});
