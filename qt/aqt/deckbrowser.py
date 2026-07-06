# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

import html
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import aqt
import aqt.operations
from anki.collection import Collection, OpChanges
from anki.decks import DeckCollapseScope, DeckId, DeckTreeNode
from aqt import AnkiQt, gui_hooks
from aqt.deckoptions import display_options_for_deck_id
from aqt.operations import QueryOp
from aqt.operations.deck import (
    add_deck_dialog,
    remove_decks,
    rename_deck,
    reparent_decks,
    set_current_deck,
    set_deck_collapsed,
)
from aqt.qt import *
from aqt.sound import av_player
from aqt.toolbar import BottomBar
from aqt.utils import getOnlyText, openLink, shortcut, showInfo, tr


class DeckBrowserBottomBar:
    def __init__(self, deck_browser: DeckBrowser) -> None:
        self.deck_browser = deck_browser


@dataclass
class RenderData:
    """Data from collection that is required to show the page."""

    tree: DeckTreeNode
    current_deck_id: DeckId
    studied_today: str
    sched_upgrade_required: bool


@dataclass
class DeckBrowserContent:
    """Stores sections of HTML content that the deck browser will be
    populated with.

    Attributes:
        tree {str} -- HTML of the deck tree section
        stats {str} -- HTML of the stats section
    """

    tree: str
    stats: str


@dataclass
class RenderDeckNodeContext:
    current_deck_id: DeckId


class DeckBrowser:
    _render_data: RenderData

    def __init__(self, mw: AnkiQt) -> None:
        self.mw = mw
        self.web = mw.web
        self.bottom = BottomBar(mw, mw.bottomWeb)
        self.scrollPos = QPoint(0, 0)
        self._refresh_needed = False

    def show(self) -> None:
        av_player.stop_and_clear_queue()
        self.web.set_bridge_command(self._linkHandler, self)
        # redraw top bar for theme change
        self.mw.toolbar.redraw()
        self.refresh()

    def refresh(self) -> None:
        # Ante replaces Anki's deck-list home screen with the readiness view.
        self._render_ante()
        self._refresh_needed = False

    def _render_ante(self) -> None:
        from aqt.ante import dashboard_body, ensure_seed_deck

        # Premade content is always ready: seed the MCAT deck on first run so
        # the den is never empty and the student never has to import anything.
        # Idempotent + main-thread (this render runs on the GUI thread).
        try:
            ensure_seed_deck(self.mw.col)
        except Exception:
            pass

        # Recreate the app: hide Anki's top/bottom toolbars so the Ante
        # single-page app fills the whole window (its own nav replaces them).
        try:
            self.mw.toolbarWeb.hide()
            self.mw.bottomWeb.hide()
        except Exception:
            pass
        # The cold-open cinematic auto-plays with narration on entry, so the
        # den's webview must not demand a user gesture first (same setting the
        # reviewer uses for card audio autoplay).
        try:
            self.web.setPlaybackRequiresGesture(False)
        except Exception:
            pass
        # Render Ante natively into Anki's main web view via stdHtml (the
        # main view is built around setHtml; a URL load does not stick).
        self.web.stdHtml(dashboard_body(), context=self)

    def refresh_if_needed(self) -> None:
        if self._refresh_needed:
            self.refresh()

    def op_executed(
        self, changes: OpChanges, handler: object | None, focused: bool
    ) -> bool:
        if changes.study_queues and handler is not self:
            self._refresh_needed = True

        if focused:
            self.refresh_if_needed()

        return self._refresh_needed

    # Event handlers
    ##########################################################################

    def _linkHandler(self, url: str) -> Any:
        if ":" in url:
            (cmd, arg) = url.split(":", 1)
        else:
            cmd = url
            arg = ""
        ante_result = self._ante_link(cmd, arg)
        if ante_result is not None:
            # returned to the page's pycmd callback as a JSON ack, so the SPA
            # can await the write instead of sleeping and hoping
            return ante_result
        if cmd == "open":
            self.set_current_deck(DeckId(int(arg)))
        elif cmd == "opts":
            self._showOptions(arg)
        elif cmd == "shared":
            self._onShared()
        elif cmd == "import":
            self.mw.onImport()
        elif cmd == "create":
            self._on_create()
        elif cmd == "drag":
            source, target = arg.split(",")
            self._handle_drag_and_drop(DeckId(int(source)), DeckId(int(target or 0)))
        elif cmd == "collapse":
            self._collapse(DeckId(int(arg)))
        elif cmd == "v2upgrade":
            self._confirm_upgrade()
        elif cmd == "v2upgradeinfo":
            self._open_v2_upgrade_info()
        elif cmd == "select":
            set_current_deck(
                parent=self.mw, deck_id=DeckId(int(arg))
            ).run_in_background()
        return False

    def _open_v2_upgrade_info(self) -> None:
        if self.mw.col.sched_ver() == 1:
            openLink("https://faqs.ankiweb.net/the-anki-2.1-scheduler.html")
        else:
            openLink("https://faqs.ankiweb.net/the-2021-scheduler.html")

    def _ante_link(self, cmd: str, arg: str) -> dict | None:
        """Dispatch the Ante single-page-app commands. Returns a small JSON-
        serializable ack if the command was an Ante one (and handled), so the
        page can await the write completing; None otherwise."""
        handlers = {
            "study": lambda: self._ante_study(),
            "ananswer": lambda: self._ante_answer(arg),
            "anadd": lambda: self._ante_add(arg),
            "anquiz": lambda: self._ante_quiz(arg),
            "anopen": lambda: self._ante_open(arg),
            "anprofile": lambda: self._ante_profile(arg),
            "anforecast": lambda: self._ante_forecast(arg),
            "anlogin": lambda: self._ante_login(arg),
            "anaccount": lambda: self._ante_account(arg),
            "andiag": lambda: self._ante_diag(arg),
            "annotify": lambda: self._ante_notify(arg),
            "angsecret": lambda: self._ante_gsecret(arg),
            "andemo": lambda: self._ante_demo(arg),
            "anfl": lambda: self._ante_fl(arg),
            "angame": lambda: self._ante_game(arg),
            "anpalace": lambda: self._ante_palace(arg),
            "anviva": lambda: self._ante_viva(arg),
            "anmap": lambda: self._ante_map(),
        }
        fn = handlers.get(cmd)
        if fn is None:
            return None
        result = fn()
        # let handlers return a richer ack (e.g. anmap's counts); default ok
        return result if isinstance(result, dict) else {"ok": True}

    def set_current_deck(self, deck_id: DeckId) -> None:
        set_current_deck(parent=self.mw, deck_id=deck_id).success(
            lambda _: self.mw.onOverview()
        ).run_in_background(initiator=self)

    # Ante: study straight from the home screen
    ##########################################################################

    def _ante_pick_deck(self) -> DeckId:
        """Study the deck that actually holds cards (the largest one)."""
        db = self.mw.col.db
        assert db is not None
        row = db.first(
            "select did, count(*) from cards group by did order by count(*) desc limit 1"
        )
        if row and row[0]:
            return DeckId(int(row[0]))
        return DeckId(1)

    def _ante_apply_order(self, did: DeckId) -> None:
        """Use the points-at-stake review order for the studied deck so the
        engine change is actually exercised during study."""
        try:
            conf = self.mw.col.decks.config_dict_for_deck_id(did)
            # REVIEW_CARD_ORDER_POINTS_AT_STAKE = 13
            if conf.get("reviewOrder") != 13:
                conf["reviewOrder"] = 13
                self.mw.col.decks.update_config(conf)
        except Exception:
            pass

    def _ante_study(self) -> None:
        # Ensure the value-ordered queue is applied, then study inside the SPA
        # (the custom Study view drives the engine via endpoints; no Anki
        # reviewer state).
        did = self._ante_pick_deck()
        self.mw.col.decks.select(did)
        self._ante_apply_order(did)

    def _ante_answer(self, arg: str) -> None:
        # arg = "<ease>[:<confidence>[:<elapsed_ms>]]" (confidence = pre-flip
        # self-rating; elapsed_ms = real think-time measured in the web view)
        from aqt.ante import answer_current_card

        try:
            parts = arg.split(":")
            conf = float(parts[1]) if len(parts) > 1 and parts[1] else None
            elapsed = int(parts[2]) if len(parts) > 2 and parts[2] else None
            answer_current_card(self.mw.col, int(parts[0]), conf, elapsed)
        except Exception:
            pass

    def _ante_quiz(self, arg: str) -> None:
        # arg = "<item_id>:<chosen_index>[:<confidence>[:<elapsed_ms>]]"
        from aqt.ante import record_quiz_answer

        try:
            parts = arg.split(":")
            confidence = float(parts[2]) if len(parts) >= 3 and parts[2] else None
            elapsed = int(parts[3]) if len(parts) >= 4 and parts[3] else None
            record_quiz_answer(
                self.mw.col, parts[0], int(parts[1]), confidence, elapsed
            )
        except Exception:
            pass

    def _ante_open(self, arg: str) -> None:
        # arg = "<item_id>:<confidence>:<elapsed_ms>:<base64(answer)>"
        import base64

        from aqt.ante import grade_and_record_open

        try:
            iid, conf_s, ms_s, ans_b64 = arg.split(":", 3)
            confidence = float(conf_s) if conf_s else None
            elapsed = int(ms_s) if ms_s else None
            answer = base64.b64decode(ans_b64.encode("ascii")).decode("utf-8")
            grade_and_record_open(self.mw.col, iid, answer, confidence, elapsed)
        except Exception:
            pass

    def _ante_profile(self, arg: str) -> None:
        # arg = base64(json(profile-updates)); saving re-applies FSRS recalibration
        import base64
        import json

        from aqt.ante import set_profile
        from aqt.utils import tooltip

        try:
            updates = json.loads(base64.b64decode(arg.encode("ascii")).decode("utf-8"))
            set_profile(self.mw.col, updates)
            if updates.get("onboarded"):
                tooltip("Plan recalibrated to your exam date.", parent=self.mw)
            self._ante_reschedule_reminders()
        except Exception:
            pass

    def _ante_reschedule_reminders(self) -> None:
        try:
            from aqt.ante_reminders import reschedule

            reschedule(self.mw)
        except Exception:
            pass

    def _ante_diag(self, arg: str) -> None:
        # arg = "done" or "skip" — close out the Baseline Diagnostic and
        # recalibrate the plan against the measured starting point.
        from aqt.ante import finish_diagnostic

        try:
            finish_diagnostic(self.mw.col, skipped=(arg == "skip"))
        except Exception:
            pass

    def _ante_notify(self, arg: str) -> None:
        # arg = "test" (next scheduled) or "fire:<kind>" (a specific type)
        try:
            if arg == "test":
                from aqt.ante_reminders import fire_test

                fire_test(self.mw)
            elif arg.startswith("fire:"):
                from aqt.ante_reminders import fire_kind

                fire_kind(self.mw, arg.split(":", 1)[1])
        except Exception:
            pass

    def _ante_demo(self, arg: str) -> None:
        # arg = "on" | "off" | "day:<n>" | "hour:<n>"
        from aqt.ante import set_demo_state

        try:
            if arg == "on":
                set_demo_state(self.mw.col, {"enabled": True})
            elif arg == "off":
                set_demo_state(self.mw.col, {"enabled": False})
            elif arg.startswith("day:"):
                set_demo_state(self.mw.col, {"day": int(arg.split(":", 1)[1])})
            elif arg.startswith("hour:"):
                set_demo_state(self.mw.col, {"hour": int(arg.split(":", 1)[1])})
        except Exception:
            pass

    def _ante_fl(self, arg: str) -> None:
        # arg = "<test_no>:<base64(json {item_id: chosen_index})>"
        import base64
        import json

        from aqt.ante import record_fl_result
        from aqt.utils import tooltip

        try:
            test_no, b64 = arg.split(":", 1)
            answers = json.loads(base64.b64decode(b64).decode("utf-8"))
            score = record_fl_result(self.mw.col, int(test_no), answers)
            tooltip(f"Full-length {score['test_no']} recorded \u2014 {score['total']}")
        except Exception:
            pass

    def _ante_game(self, arg: str) -> None:
        # arg = "save:<game_id>:<base64(json state)>" | "clear:<game_id>" —
        # in-progress game snapshots, so leaving a game never restarts it
        import base64
        import json

        from aqt.ante import clear_game_state, save_game_state

        try:
            act, _, rest = arg.partition(":")
            if act == "save":
                gid, _, b64 = rest.partition(":")
                state = json.loads(
                    base64.b64decode(b64.encode("ascii")).decode("utf-8")
                )
                if gid and isinstance(state, dict):
                    save_game_state(self.mw.col, gid, state)
            elif act == "clear" and rest:
                clear_game_state(self.mw.col, rest)
        except Exception:
            pass

    def _ante_palace(self, arg: str) -> None:
        # arg = "commission" (render new scenes for leeches) | "regen:<card_id>"
        from aqt.ante_studio import commission_palace_async, regenerate_scene

        try:
            if arg == "commission":
                commission_palace_async(self.mw, count=3)
            elif arg.startswith("regen:"):
                regenerate_scene(self.mw, int(arg.split(":", 1)[1]))
        except Exception:
            pass

    def _ante_viva(self, arg: str) -> None:
        # arg = "start:<b64topic>" | "answer:<b64answer>" | "live:<on|off>"
        #     | "close"
        import base64

        def dec(x: str) -> str:
            return base64.b64decode(x.encode("ascii")).decode("utf-8") if x else ""

        try:
            from aqt.ante import get_demo_state

            demo = bool(get_demo_state(self.mw.col).get("enabled"))
            act, _, val = arg.partition(":")
            if act == "start":
                self._ante_viva_start(dec(val), demo)
            elif act == "answer":
                self._ante_viva_answer(dec(val), demo)
            elif act == "live":
                # the web client joined/left the live (Realtime) table; while
                # live, the turn-based TTS clips stay quiet
                from aqt.ante_studio import set_viva_live_voice

                if not demo:
                    set_viva_live_voice(self.mw.col, val == "on")
            elif act == "close":
                from aqt.ante_studio import _set_active_viva

                _set_active_viva(self.mw.col, None)
        except Exception:
            pass

    def _ante_viva_start(self, topic: str, demo: bool) -> None:
        from aqt.ante_studio import commission_say_async, start_viva

        start_viva(self.mw.col, topic)
        # Sahir speaks his opening line (cached -> instant; cold -> rendered
        # in the background while the student reads it)
        if not demo:
            commission_say_async(self.mw)

    def _ante_viva_answer(self, answer: str, demo: bool) -> None:
        from aqt.ante_studio import (
            answer_viva,
            commission_say_async,
            commission_verdict_async,
        )

        session = answer_viva(self.mw.col, answer)
        # demo never commissions real media (it can't write to studio)
        if demo or not session:
            return
        if session.get("status") in ("passed", "failed"):
            commission_verdict_async(self.mw, session)
        else:
            # a probe came back — give it Sahir's voice too
            commission_say_async(self.mw)

    def _ante_map(self) -> dict:
        # Seat a third-party deck: tag untagged notes onto Circuit topics.
        from aqt.ante import map_untagged_notes

        try:
            return map_untagged_notes(self.mw.col)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _ante_gsecret(self, arg: str) -> None:
        # arg = base64(client secret) — stored device-locally for the Google
        # 'Desktop app' token exchange (which requires the client secret).
        import base64

        from aqt.ante import set_google_secret

        try:
            secret = (
                base64.b64decode(arg.encode("ascii")).decode("utf-8") if arg else ""
            )
            set_google_secret(self.mw.col, secret)
        except Exception:
            pass

    def _ante_login(self, arg: str) -> None:
        # arg = "google[:<b64email>]" or "email:<b64email>[:<b64name>]"
        import base64

        from aqt.ante import sign_in_email

        def dec(x: str) -> str:
            return base64.b64decode(x.encode("ascii")).decode("utf-8") if x else ""

        try:
            kind, _, rest = arg.partition(":")
            if kind == "google":
                from aqt import ante_auth

                ante_auth.start_google_login(self.mw, dec(rest))
            elif kind == "email":
                e, _, n = rest.partition(":")
                sign_in_email(self.mw.col, dec(e), dec(n) or None)
        except Exception:
            pass

    def _ante_account(self, arg: str) -> None:
        # arg = "switch:<b64id>" or "signout"
        import base64

        from aqt.ante import sign_out, switch_account

        try:
            act, _, val = arg.partition(":")
            if act == "switch" and val:
                switch_account(
                    self.mw.col, base64.b64decode(val.encode("ascii")).decode("utf-8")
                )
            elif act == "signout":
                sign_out(self.mw.col)
        except Exception:
            pass

    def _ante_forecast(self, arg: str) -> None:
        # arg = "<exam_date YYYY-MM-DD or ''>:<target_score or ''>"
        from aqt.ante import set_forecast_settings

        try:
            date_str, _, target_str = arg.partition(":")
            exam_date = date_str.strip() or None
            target = int(target_str) if target_str.strip() else None
            set_forecast_settings(self.mw.col, exam_date, target)
        except Exception:
            pass

    def _ante_add(self, arg: str) -> None:
        import json
        import urllib.parse

        from aqt.ante import add_note_from_payload
        from aqt.utils import tooltip

        try:
            payload = json.loads(urllib.parse.unquote(arg))
            result = add_note_from_payload(self.mw.col, payload)
            if result.get("demo"):
                tooltip("Card added (demo mode \u2014 not saved to a real deck)")
            elif result.get("ok"):
                tooltip("Card added")
            else:
                tooltip(f"Add failed: {result.get('error')}")
        except Exception as e:
            tooltip(f"Add failed: {e}")

    # HTML generation
    ##########################################################################

    _body = """
<center>
<table cellspacing=0 cellpadding=3>
%(tree)s
</table>

<br>
%(stats)s
</center>
"""

    def _renderPage(self, reuse: bool = False) -> None:
        if not reuse:

            def get_data(col: Collection) -> RenderData:
                return RenderData(
                    tree=col.sched.deck_due_tree(),
                    current_deck_id=col.decks.get_current_id(),
                    studied_today=col.studied_today(),
                    sched_upgrade_required=not col.v3_scheduler(),
                )

            def success(output: RenderData) -> None:
                self._render_data = output
                self.__renderPage(None)

            QueryOp(
                parent=self.mw,
                op=get_data,
                success=success,
            ).run_in_background()
        else:
            self.web.evalWithCallback("window.pageYOffset", self.__renderPage)

    def __renderPage(self, offset: int | None) -> None:
        data = self._render_data
        content = DeckBrowserContent(
            tree=self._renderDeckTree(data.tree),
            stats=self._renderStats(),
        )
        gui_hooks.deck_browser_will_render_content(self, content)
        self.web.stdHtml(
            self._v1_upgrade_message(data.sched_upgrade_required)
            + self._body % content.__dict__,
            css=["css/deckbrowser.css"],
            js=[
                "js/vendor/jquery.min.js",
                "js/vendor/jquery-ui.min.js",
                "js/deckbrowser.js",
            ],
            context=self,
        )
        self._drawButtons()
        if offset is not None:
            self._scrollToOffset(offset)
        gui_hooks.deck_browser_did_render(self)

    def _scrollToOffset(self, offset: int) -> None:
        self.web.eval("window.scrollTo(0, %d, 'instant');" % offset)

    def _renderStats(self) -> str:
        return '<div id="studiedToday"><span>{}</span></div>'.format(
            self._render_data.studied_today
        )

    def _renderDeckTree(self, top: DeckTreeNode) -> str:
        buf = """
<tr><th colspan=5 align=start>{}</th>
<th class=count>{}</th>
<th class=count>{}</th>
<th class=count>{}</th>
<th class=optscol></th></tr>""".format(
            tr.decks_deck(),
            tr.actions_new(),
            tr.decks_learn_header(),
            tr.decks_review_header(),
        )
        buf += self._topLevelDragRow()

        ctx = RenderDeckNodeContext(current_deck_id=self._render_data.current_deck_id)

        for child in top.children:
            buf += self._render_deck_node(child, ctx)

        return buf

    def _render_deck_node(self, node: DeckTreeNode, ctx: RenderDeckNodeContext) -> str:
        if node.collapsed:
            prefix = "+"
        else:
            prefix = "−"

        def indent() -> str:
            return "&nbsp;" * 6 * (node.level - 1)

        if node.deck_id == ctx.current_deck_id:
            klass = "deck current"
        else:
            klass = "deck"

        buf = (
            "<tr class='%s' id='%d' onclick='if(event.shiftKey) return pycmd(\"select:%d\")'>"
            % (
                klass,
                node.deck_id,
                node.deck_id,
            )
        )
        # deck link
        if node.children:
            collapse = (
                "<a class=collapse href=# onclick='return pycmd(\"collapse:%d\")'>%s</a>"
                % (node.deck_id, prefix)
            )
        else:
            collapse = "<span class=collapse></span>"
        if node.filtered:
            extraclass = "filtered"
        else:
            extraclass = ""
        buf += """

        <td class=decktd colspan=5>%s%s<a class="deck %s"
        href=# onclick="return pycmd('open:%d')">%s</a></td>""" % (
            indent(),
            collapse,
            extraclass,
            node.deck_id,
            html.escape(node.name),
        )

        # due counts
        def nonzeroColour(cnt: int, klass: str) -> str:
            if not cnt:
                klass = "zero-count"
            return f'<span class="{klass}">{cnt}</span>'

        review = nonzeroColour(node.review_count, "review-count")
        learn = nonzeroColour(node.learn_count, "learn-count")

        buf += ("<td align=end>%s</td>" * 3) % (
            nonzeroColour(node.new_count, "new-count"),
            learn,
            review,
        )
        # options
        buf += (
            "<td align=center class=opts><a onclick='return pycmd(\"opts:%d\");'>"
            "<img src='/_anki/imgs/gears.svg' class=gears></a></td></tr>" % node.deck_id
        )
        # children
        if not node.collapsed:
            for child in node.children:
                buf += self._render_deck_node(child, ctx)
        return buf

    def _topLevelDragRow(self) -> str:
        return "<tr class='top-level-drag-row'><td colspan='6'>&nbsp;</td></tr>"

    # Options
    ##########################################################################

    def _showOptions(self, did: str) -> None:
        m = QMenu(self.mw)
        a = m.addAction(tr.actions_rename())
        assert a is not None
        qconnect(a.triggered, lambda b, did=did: self._rename(DeckId(int(did))))
        a = m.addAction(tr.actions_options())
        assert a is not None
        qconnect(a.triggered, lambda b, did=did: self._options(DeckId(int(did))))
        a = m.addAction(tr.actions_export())
        assert a is not None
        qconnect(a.triggered, lambda b, did=did: self._export(DeckId(int(did))))
        a = m.addAction(tr.actions_delete())
        assert a is not None
        qconnect(a.triggered, lambda b, did=did: self._delete(DeckId(int(did))))
        gui_hooks.deck_browser_will_show_options_menu(m, int(did))
        m.popup(QCursor.pos())

    def _export(self, did: DeckId) -> None:
        self.mw.onExport(did=did)

    def _rename(self, did: DeckId) -> None:
        def prompt(name: str) -> None:
            new_name = getOnlyText(
                tr.decks_new_deck_name(), default=name, title=tr.actions_rename()
            )
            if not new_name or new_name == name:
                return
            else:
                rename_deck(
                    parent=self.mw, deck_id=did, new_name=new_name
                ).run_in_background()

        QueryOp(
            parent=self.mw, op=lambda col: col.decks.name(did), success=prompt
        ).run_in_background()

    def _options(self, did: DeckId) -> None:
        display_options_for_deck_id(did)

    def _collapse(self, did: DeckId) -> None:
        node = self.mw.col.decks.find_deck_in_tree(self._render_data.tree, did)
        if node:
            node.collapsed = not node.collapsed
            set_deck_collapsed(
                parent=self.mw,
                deck_id=did,
                collapsed=node.collapsed,
                scope=DeckCollapseScope.REVIEWER,
            ).run_in_background()
            self._renderPage(reuse=True)

    def _handle_drag_and_drop(self, source: DeckId, target: DeckId) -> None:
        reparent_decks(
            parent=self.mw, deck_ids=[source], new_parent=target
        ).run_in_background()

    def _delete(self, did: DeckId) -> None:
        deck = self.mw.col.decks.find_deck_in_tree(self._render_data.tree, did)
        assert deck is not None
        deck_name = deck.name
        remove_decks(
            parent=self.mw, deck_ids=[did], deck_name=deck_name
        ).run_in_background()

    # Top buttons
    ######################################################################

    drawLinks = [
        ["", "study", "Take your seat"],
        ["Ctrl+Shift+I", "import", tr.decks_import_file()],
    ]

    def _drawButtons(self) -> None:
        buf = ""
        drawLinks = deepcopy(self.drawLinks)
        for b in drawLinks:
            if b[0]:
                b[0] = tr.actions_shortcut_key(val=shortcut(b[0]))
            buf += """
<button title='%s' onclick='pycmd(\"%s\");'>%s</button>""" % tuple(b)
        self.bottom.draw(
            buf=buf,
            link_handler=self._linkHandler,
            web_context=DeckBrowserBottomBar(self),
        )

    def _onShared(self) -> None:
        openLink(f"{aqt.appShared}decks/")

    def _on_create(self) -> None:
        if op := add_deck_dialog(
            parent=self.mw, default_text=self.mw.col.decks.current()["name"]
        ):
            op.run_in_background()

    ######################################################################

    def _v1_upgrade_message(self, required: bool) -> str:
        if not required:
            return ""

        update_required = tr.scheduling_update_required().replace("V2", "v3")

        return f"""
<center>
<div class=callout>
    <div>
      {update_required}
    </div>
    <div>
      <button onclick='pycmd("v2upgrade")'>
        {tr.scheduling_update_button()}
      </button>
      <button onclick='pycmd("v2upgradeinfo")'>
        {tr.scheduling_update_more_info_button()}
      </button>
    </div>
</div>
</center>
"""

    def _confirm_upgrade(self) -> None:
        if self.mw.col.sched_ver() == 1:
            self.mw.col.mod_schema(check=True)
            self.mw.col.upgrade_to_v2_scheduler()
        self.mw.col.set_v3_scheduler(True)

        showInfo(tr.scheduling_update_done())
        self.refresh()
