// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
//
// Host-side smoke test for the Swift engine bridge (`just ios-swift-smoke`).
//
// Compiles the PRODUCTION files — ProtoWire, BackendMessages, BackendIndices,
// AnkiEngine, EngineClient (SyncedEngine), Models — into a macOS executable
// linked against the host build of libanki_engine.a, and drives them exactly
// the way the phone does. No Xcode project, no simulator, no mocks.
//
// Stage 1 (always): open the backend + an empty collection, read mastery/due
// through the hand-rolled protobuf codec, answer-path guards.
//
// Stage 2 (when SYNC_ENDPOINT is set): join a real self-hosted sync server
// (`just sync-server`), full-download the den's collection, deal cards, answer
// one through the real scheduler, and sync the review back up — the identical
// call sequence the iOS app runs.

import Foundation

@main
struct HostSmoke {
    static func main() async {
        do {
            try await run()
            print("HOST-SMOKE PASS")
        } catch {
            print("HOST-SMOKE FAIL: \(error)")
            exit(1)
        }
    }

    static func check(_ condition: Bool, _ label: String) throws {
        if condition {
            print("  ok: \(label)")
        } else {
            throw SmokeError.failed(label)
        }
    }

    enum SmokeError: Error, CustomStringConvertible {
        case failed(String)
        var description: String {
            if case .failed(let label) = self { return "check failed: \(label)" }
            return "unknown"
        }
    }

    static func run() async throws {
        let engine = SyncedEngine()
        // Stage 1: the embedded engine boots and serves the empty collection.
        guard let status = await engine.liveStatus() else {
            throw SmokeError.failed("live engine did not start")
        }
        print("engine build \(status.buildHash)")
        try check(!status.buildHash.isEmpty, "buildhash readable through FFI")

        await engine.disconnect()
        let emptyMastery = try await engine.fetchMastery()
        try check(emptyMastery.isEmpty, "empty collection lists no tables")
        let emptyDue = try await engine.fetchDue()
        try check(emptyDue.cards.isEmpty, "empty collection deals no cards")
        let scores = try await engine.fetchScores()
        try check(scores.readiness.abstained, "readiness abstains (NO LINE) on no evidence")
        try check(scores.memory.abstained, "memory abstains on the empty seat")

        // Stage 2: the real den, when a sync server is provided.
        let env = ProcessInfo.processInfo.environment
        guard let endpoint = env["SYNC_ENDPOINT"], !endpoint.isEmpty else {
            print("SYNC_ENDPOINT unset — skipping live sync stage")
            return
        }
        let user = env["SYNC_USER"] ?? "ante"
        let pass = env["SYNC_PASS"] ?? "ante123"

        let line = try await engine.connect(endpoint: endpoint, username: user, password: pass)
        print("  connect: \(line)")
        let mastery = try await engine.fetchMastery()
        try check(!mastery.isEmpty, "tables listed after full download (\(mastery.count) topics)")

        let due = try await engine.fetchDue()
        print("  due: \(due.dueCardCount) cards outstanding, \(due.cards.count) dealt")
        try check(!due.cards.isEmpty, "queue deals cards")
        let first = due.cards[0]
        try check(!first.question.isEmpty, "dealt card renders a question")
        try check(first.topic.hasPrefix("mcat::"), "dealt card carries its topic tag")

        try await engine.answer(cardID: first.id, rating: 2, millisecondsTaken: 2500)
        print("  answered card \(first.id) (Good) through the real scheduler")

        let syncLine = try await engine.sync()
        print("  sync: \(syncLine)")
        let after = try await engine.fetchDue()
        try check(
            after.dueCardCount <= due.dueCardCount,
            "outstanding count did not grow after answering (\(due.dueCardCount) -> \(after.dueCardCount))"
        )
    }
}
