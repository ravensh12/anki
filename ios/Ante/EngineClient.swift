// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
//
// The boundary between the UI and the study engine. Everything the app shows —
// the three scores, the mastery map, the due stack — comes through this one
// protocol, so the views never care whether the data is mocked or is coming from
// the real Rust core over the FFI seam.
//
//   * MockEngine   — deterministic sample data (previews, and the escape hatch
//                    when the FFI backend cannot start).
//   * SyncedEngine — the LIVE client: the shared `anki` Rust crate compiled
//                    into AnkiEngine.xcframework, driven over the same single
//                    `run_service_method` protobuf seam the desktop uses, and
//                    synced two-way against the self-hosted Anki sync server.

import Foundation

// MARK: - Due work returned to a session

/// A ready-to-play batch of scheduled work: recall cards plus the application
/// items that get interleaved, in points-at-stake order (weakest, highest-value
/// first). `dueCardCount` / `dueItemCount` are the full outstanding counts; the
/// arrays are the slice loaded for this session.
struct DueQueue {
    var cards: [ReviewCard]
    var items: [ApplicationItem]
    var dueCardCount: Int
    var dueItemCount: Int
    var bestNextTopic: String?

    static let empty = DueQueue(
        cards: [],
        items: [],
        dueCardCount: 0,
        dueItemCount: 0,
        bestNextTopic: nil
    )
}

// MARK: - Live-engine surface

/// How this seat is wired: which engine build is embedded, and which den
/// (sync server account) it is joined to, if any.
struct EngineStatus: Equatable {
    var buildHash: String
    var endpoint: String?
    var username: String?

    var connected: Bool { endpoint != nil }
}

// MARK: - The engine boundary

protocol EngineClient {
    /// The three separate, honest scores (readiness abstains until it has evidence).
    func fetchScores() async throws -> ScoresSnapshot
    /// Per-topic comprehension for the Atlas, scored only from application.
    func fetchMastery() async throws -> [TopicMastery]
    /// The scheduled due stack for a session, in points-at-stake order.
    func fetchDue() async throws -> DueQueue

    // Live-engine capabilities. Sample engines keep the defaults below.

    /// nil when this client serves sample data; a status once the Rust core runs.
    func liveStatus() async -> EngineStatus?
    /// Join a den: log into the sync server, remember the key, first sync.
    func connect(endpoint: String, username: String, password: String) async throws -> String
    /// Forget the den (local collection stays).
    func disconnect() async
    /// Two-way sync against the joined den. Returns a short honest status line.
    func sync() async throws -> String
    /// Answer a dealt card through the real scheduler (rating 0–3 = Again…Easy).
    func answer(cardID: String, rating: Int, millisecondsTaken: Int) async throws
}

enum EngineClientError: LocalizedError {
    case unsupported
    case notConnected
    case unknownCard

    var errorDescription: String? {
        switch self {
        case .unsupported: return "This build serves sample data — there is no live engine behind it."
        case .notConnected: return "Not joined to a den yet — connect to your sync server first."
        case .unknownCard: return "That card is no longer in the dealt batch — pull fresh work."
        }
    }
}

extension EngineClient {
    func liveStatus() async -> EngineStatus? { nil }
    func connect(endpoint: String, username: String, password: String) async throws -> String {
        throw EngineClientError.unsupported
    }
    func disconnect() async {}
    func sync() async throws -> String { throw EngineClientError.unsupported }
    func answer(cardID: String, rating: Int, millisecondsTaken: Int) async throws {}
}

// MARK: - Mock engine

/// Deterministic sample data with the product's honesty built in: memory and
/// performance read out, but readiness abstains because the application evidence
/// is thin. A short delay simulates engine/sync latency so the UI exercises its
/// loading states.
final class MockEngine: EngineClient {
    private let latency: Duration

    init(latency: Duration = .milliseconds(250)) {
        self.latency = latency
    }

    func fetchScores() async throws -> ScoresSnapshot {
        try await Task.sleep(for: latency)
        return Self.sampleScores
    }

    func fetchMastery() async throws -> [TopicMastery] {
        try await Task.sleep(for: latency)
        return Self.sampleMastery
    }

    func fetchDue() async throws -> DueQueue {
        try await Task.sleep(for: latency)
        return Self.sampleQueue
    }

    // MARK: Sample data

    static let sampleScores = ScoresSnapshot(
        memory: ScoreReading(
            kind: .memory,
            value: 0.72,
            lower: 0.66,
            upper: 0.78,
            abstained: false,
            confidence: "medium",
            reasons: ["FSRS recall across 1,140 reviewed cards."]
        ),
        performance: ScoreReading(
            kind: .performance,
            value: 0.58,
            lower: 0.49,
            upper: 0.67,
            abstained: false,
            confidence: "low",
            reasons: ["Beats the memory baseline on paraphrased items, but the sample is small."]
        ),
        readiness: ScoreReading(
            kind: .readiness,
            value: nil,
            lower: nil,
            upper: nil,
            abstained: true,
            confidence: nil,
            reasons: [
                "Only 37 graded application answers — the give-up rule needs 200.",
                "12% weighted coverage, with an untested blind spot in Chem / Phys.",
                "A confident number with nothing behind it is a guess in a nice font.",
            ]
        ),
        overallComprehension: 0.44,
        evidencedFraction: 0.31,
        selfTrust: 63
    )

    static let sampleMastery: [TopicMastery] = [
        // Bio / Biochem
        topic("bio_biochem", "amino_acids", "Amino acids, peptides, proteins", .mastered, 0.91, 0.86, 0.95, 0.12),
        topic("bio_biochem", "protein_structure", "Protein structure & function", .active, 0.68, 0.58, 0.75, 0.10),
        topic("bio_biochem", "enzymes", "Enzymes & kinetics", .corrective, 0.51, 0.38, 0.64, 0.12, overconfidence: 0.13),
        topic("bio_biochem", "carbohydrates", "Carbohydrates", .mastered, 0.87, 0.81, 0.92, 0.06),
        topic("bio_biochem", "cell_biology", "Cell biology & membranes", .active, 0.62, 0.52, 0.70, 0.08),
        topic("bio_biochem", "glycolysis", "Glycolysis", .locked, nil, nil, nil, 0.07),
        topic("bio_biochem", "citric_acid_cycle", "Citric acid cycle", .locked, nil, nil, nil, 0.07),
        // Chem / Phys
        topic("chem_phys", "atomic_structure", "Atomic structure", .mastered, 0.84, 0.78, 0.90, 0.10),
        topic("chem_phys", "bonding", "Bonding & molecular structure", .active, 0.59, 0.49, 0.68, 0.09),
        topic("chem_phys", "thermodynamics", "Thermodynamics", .corrective, 0.47, 0.33, 0.60, 0.10, overconfidence: 0.16),
        topic("chem_phys", "acids_bases", "Acids & bases", .active, 0.66, 0.57, 0.74, 0.10),
        topic("chem_phys", "kinematics", "Kinematics", .locked, nil, nil, nil, 0.08),
        topic("chem_phys", "circuits", "Electrostatics & circuits", .locked, nil, nil, nil, 0.07),
        // Psych / Soc
        topic("psych_soc", "sensation_perception", "Sensation & perception", .active, 0.70, 0.61, 0.77, 0.12),
        topic("psych_soc", "learning_memory", "Learning & memory", .mastered, 0.89, 0.83, 0.94, 0.11),
        topic("psych_soc", "cognition", "Cognition & language", .active, 0.63, 0.53, 0.71, 0.10),
        topic("psych_soc", "social_processes", "Social processes & behavior", .locked, nil, nil, nil, 0.10),
        // CARS (a single skills bucket)
        topic("cars", "reasoning", "Critical analysis & reasoning", .active, 0.55, 0.44, 0.65, 1.0, overconfidence: 0.09),
    ]

    static let sampleQueue = DueQueue(
        cards: [
            ReviewCard(
                id: "c1",
                topic: "mcat::chem_phys::thermodynamics",
                question: "For a spontaneous process at constant T and P, what is the sign of ΔG?",
                answer: "Negative. ΔG < 0 means the process releases free energy and proceeds without added work."
            ),
            ReviewCard(
                id: "c2",
                topic: "mcat::bio_biochem::enzymes",
                question: "How does a competitive inhibitor affect apparent Km and Vmax?",
                answer: "Apparent Km rises; Vmax is unchanged. Enough substrate outcompetes the inhibitor."
            ),
            ReviewCard(
                id: "c3",
                topic: "mcat::bio_biochem::amino_acids",
                question: "Which of the twenty standard amino acids is achiral?",
                answer: "Glycine — its side chain is a single hydrogen, so the alpha carbon is not a stereocenter."
            ),
            ReviewCard(
                id: "c4",
                topic: "mcat::chem_phys::acids_bases",
                question: "In the Henderson–Hasselbalch equation, what is pH when [A⁻] equals [HA]?",
                answer: "pH = pKa. The ratio is 1 and log(1) = 0, so the buffer is at its center."
            ),
            ReviewCard(
                id: "c5",
                topic: "mcat::psych_soc::sensation_perception",
                question: "What does Weber's law state about the just-noticeable difference?",
                answer: "The JND is a constant proportion of the original stimulus intensity, not a fixed amount."
            ),
        ],
        items: [
            ApplicationItem(
                id: "q1",
                topic: "mcat::chem_phys::thermodynamics",
                stem: "A reaction has ΔH = +40 kJ/mol and ΔS = +150 J/(mol·K). At what temperatures is it spontaneous?",
                choices: [
                    "At all temperatures",
                    "Only above ~267 K",
                    "Only below ~267 K",
                    "At no temperature",
                ],
                correctIndex: 1,
                isRetest: false
            ),
            ApplicationItem(
                id: "q2",
                topic: "mcat::bio_biochem::enzymes",
                stem: "A noncompetitive inhibitor binds an allosteric site. On a Lineweaver–Burk plot versus the uninhibited enzyme, what changes?",
                choices: [
                    "Vmax decreases; Km unchanged",
                    "Vmax unchanged; Km increases",
                    "Both Vmax and Km increase",
                    "Neither changes",
                ],
                correctIndex: 0,
                isRetest: true
            ),
        ],
        dueCardCount: 18,
        dueItemCount: 4,
        bestNextTopic: "mcat::chem_phys::thermodynamics"
    )

    /// Small builder so the sample rows above stay readable.
    private static func topic(
        _ section: String,
        _ leaf: String,
        _ name: String,
        _ status: MasteryStatus,
        _ comprehension: Double?,
        _ bandLower: Double?,
        _ bandUpper: Double?,
        _ examWeight: Double,
        overconfidence: Double = 0
    ) -> TopicMastery {
        TopicMastery(
            tag: "mcat::\(section)::\(leaf)",
            name: name,
            section: MCATSection(rawValue: section) ?? .bioBiochem,
            status: status,
            comprehension: comprehension,
            bandLower: bandLower,
            bandUpper: bandUpper,
            examWeight: examWeight,
            hasEvidence: comprehension != nil,
            overconfidence: overconfidence
        )
    }
}

// MARK: - The live shared-engine client

/// The production engine client. It:
///
/// 1. Opens the local `.anki2` collection through the shared Rust backend inside
///    AnkiEngine.xcframework — the exact same modified crate the desktop uses,
///    not a re-implementation.
/// 2. Encodes each request protobuf by hand (ProtoWire/BackendMessages), hands
///    the bytes to `run_service_method` via the C seam, and decodes the response
///    into the Swift model types. `fetchMastery()` is a direct call to the Ante
///    engine change (`GetTopicMastery`); `fetchDue()` walks `GetQueuedCards` —
///    already in points-at-stake order when the deck preset uses the Ante queue.
/// 3. Syncs two-way against the self-hosted Anki sync server, mirroring the
///    proven sequence in `ante/tools/sync_test.py` (login → sync_collection →
///    full download/upload when required → reopen).
///
/// If the backend cannot start at all, it falls back to sample data so the app
/// still opens (and says so through `liveStatus()` returning nil).
actor SyncedEngine: EngineClient {
    private let engine: AnkiEngine?
    private let fallback: EngineClient

    /// UserDefaults-persisted link to the den (endpoint + username + hkey).
    private struct Connection: Codable {
        var endpoint: String
        var username: String
        var hkey: String
    }

    private static let connectionKey = "ante.engine.connection.v1"
    private var connection: Connection?
    private var collectionReady = false
    /// Whether we've seated this run at the deck that holds the cards.
    private var deckSelected = false
    /// The last dealt batch, kept so an answer can echo the exact scheduling
    /// states the engine dealt with the card (see PBQueuedCard).
    private var dealtByID: [String: PBQueuedCard] = [:]

    init(fallback: EngineClient = MockEngine()) {
        self.fallback = fallback
        if ProcessInfo.processInfo.environment["ANTE_SAMPLE_DATA"] == "1" {
            self.engine = nil
        } else {
            self.engine = try? AnkiEngine()
        }
        if let data = UserDefaults.standard.data(forKey: Self.connectionKey),
            let saved = try? JSONDecoder().decode(Connection.self, from: data)
        {
            connection = saved
        }
    }

    // MARK: Lifecycle

    private func ensureCollection() async throws {
        guard let engine, !collectionReady else { return }
        try await engine.openCollection(in: AnkiEngine.collectionDirectory())
        collectionReady = true
    }

    func liveStatus() async -> EngineStatus? {
        guard engine != nil else { return nil }
        return EngineStatus(
            buildHash: AnkiEngine.buildHash,
            endpoint: connection?.endpoint,
            username: connection?.username
        )
    }

    // MARK: Joining a den + sync

    func connect(endpoint: String, username: String, password: String) async throws -> String {
        guard let engine else { throw EngineClientError.unsupported }
        try await ensureCollection()
        let response = try await engine.run(
            BackendIndices.syncLogin,
            PBSyncLoginRequest.encode(username: username, password: password, endpoint: endpoint)
        )
        let auth = try PBSyncAuth.decode(response)
        connection = Connection(
            endpoint: auth.endpoint.isEmpty ? endpoint : auth.endpoint,
            username: username,
            hkey: auth.hkey
        )
        persistConnection()
        let line = try await sync()
        return "Seat linked. \(line)"
    }

    func disconnect() async {
        connection = nil
        persistConnection()
    }

    /// Mirrors ante/tools/sync_test.py: sync_collection performs the normal
    /// sync; FULL_DOWNLOAD/FULL_SYNC pull the server's canonical copy (the
    /// server holds the den's truth); FULL_UPLOAD pushes ours. After a full
    /// transfer the collection file was swapped out underneath the backend,
    /// so close + reopen before anyone reads again.
    func sync() async throws -> String {
        guard let engine else { throw EngineClientError.unsupported }
        guard let connection else { throw EngineClientError.notConnected }
        try await ensureCollection()
        let auth = PBSyncAuth(hkey: connection.hkey, endpoint: connection.endpoint)
        let response = try await engine.run(
            BackendIndices.syncCollection,
            PBSyncCollectionRequest.encode(auth: auth, syncMedia: false)
        )
        let outcome = try PBSyncCollectionResponse.decode(response)
        if !outcome.newEndpoint.isEmpty, outcome.newEndpoint != connection.endpoint {
            self.connection?.endpoint = outcome.newEndpoint
            persistConnection()
        }
        dealtByID.removeAll()
        switch outcome.required {
        case .noChanges, .normalSync:
            // sync_collection already exchanged any changes; "no changes" is
            // what REMAINS required, not what happened.
            return "Synced — this seat is current."
        case .fullDownload, .fullSync:
            _ = try await engine.run(
                BackendIndices.fullUploadOrDownload,
                PBFullUploadOrDownloadRequest.encode(auth: auth, upload: false)
            )
            await reopenCollection()
            return "Pulled the den's full collection."
        case .fullUpload:
            _ = try await engine.run(
                BackendIndices.fullUploadOrDownload,
                PBFullUploadOrDownloadRequest.encode(auth: auth, upload: true)
            )
            await reopenCollection()
            return "Uploaded this seat's collection to the den."
        }
    }

    private func reopenCollection() async {
        guard let engine else { return }
        await engine.closeCollection()
        collectionReady = false
        deckSelected = false
        try? await ensureCollection()
    }

    private func persistConnection() {
        let defaults = UserDefaults.standard
        if let connection, let data = try? JSONEncoder().encode(connection) {
            defaults.set(data, forKey: Self.connectionKey)
        } else {
            defaults.removeObject(forKey: Self.connectionKey)
        }
    }

    // MARK: Reads

    func fetchMastery() async throws -> [TopicMastery] {
        guard engine != nil else { return try await fallback.fetchMastery() }
        return try await masteryRows().map(Self.uiTopic)
    }

    func fetchScores() async throws -> ScoresSnapshot {
        guard engine != nil else { return try await fallback.fetchScores() }
        return Self.scores(from: try await masteryRows(), connected: connection != nil)
    }

    func fetchDue() async throws -> DueQueue {
        guard let engine else { return try await fallback.fetchDue() }
        try await ensureCollection()
        try await seatAtStudyDeck()
        let queued = try PBQueuedCards.decode(
            try await engine.run(
                BackendIndices.getQueuedCards,
                PBGetQueuedCardsRequest.encode(fetchLimit: 20)
            )
        )
        dealtByID = Dictionary(
            uniqueKeysWithValues: queued.cards.map { (String($0.card.id), $0) })

        var cards: [ReviewCard] = []
        var noteCache: [Int64: PBNote] = [:]
        for dealt in queued.cards {
            let noteID = dealt.card.noteID
            if noteCache[noteID] == nil {
                let raw = try await engine.run(BackendIndices.getNote, PBNoteID.encode(noteID))
                noteCache[noteID] = try PBNote.decode(raw)
            }
            guard let note = noteCache[noteID] else { continue }
            cards.append(Self.uiCard(id: dealt.card.id, note: note))
        }
        return DueQueue(
            cards: cards,
            items: [],
            dueCardCount: queued.totalDue,
            dueItemCount: 0,
            bestNextTopic: cards.first?.topic
        )
    }

    /// The queue deals from the CURRENT deck, which on a fresh seat is the
    /// empty Default. Mirror the desktop den's `_ante_pick_deck`: select the
    /// deck that actually holds the cards (largest, children included). Once
    /// per run, and again after a sync swaps the collection.
    private func seatAtStudyDeck() async throws {
        guard let engine, !deckSelected else { return }
        // A real timestamp matters: with now=0 the engine skips computing
        // counts and every deck reads empty.
        let tree = try PBDeckTreeNode.decode(
            try await engine.run(
                BackendIndices.deckTree,
                PBDeckTreeRequest.encode(now: Int64(Date().timeIntervalSince1970))
            )
        )
        if let best = tree.children.max(by: { $0.totalIncludingChildren < $1.totalIncludingChildren }),
            best.totalIncludingChildren > 0
        {
            _ = try await engine.run(BackendIndices.setCurrentDeck, PBDeckID.encode(best.deckID))
        }
        deckSelected = true
    }

    // MARK: Answering

    func answer(cardID: String, rating: Int, millisecondsTaken: Int) async throws {
        guard let engine else { return }
        guard let dealt = dealtByID[cardID] else { throw EngineClientError.unknownCard }
        guard let pbRating = PBRating(rawValue: UInt64(max(0, min(3, rating)))) else { return }
        let request = PBCardAnswer.encode(
            cardID: dealt.card.id,
            currentState: dealt.statesCurrent,
            newState: dealt.newState(for: pbRating),
            rating: pbRating,
            answeredAtMillis: Int64(Date().timeIntervalSince1970 * 1000),
            millisecondsTaken: UInt32(max(0, min(millisecondsTaken, 10 * 60 * 1000)))
        )
        _ = try await engine.run(BackendIndices.answerCard, request)
        dealtByID.removeValue(forKey: cardID)
    }

    // MARK: Engine rows -> UI models

    private func masteryRows() async throws -> [PBTopicMastery] {
        guard let engine else { return [] }
        try await ensureCollection()
        let raw = try await engine.run(
            BackendIndices.getTopicMastery,
            PBGetTopicMasteryRequest.encode(search: "", topicPrefix: "mcat::", threshold: 0.9)
        )
        return try PBGetTopicMasteryResponse.decode(raw).topics
    }

    /// Map an engine mastery row to the Atlas tile. The engine's signal here is
    /// FSRS recall (memory) — real, per-topic, from the shared crate. The
    /// desktop's application-gated comprehension/bands need the den's response
    /// logs, which don't sync to the phone yet, so tables without study stay
    /// honestly unlisted and no bands are invented.
    static func uiTopic(_ row: PBTopicMastery) -> TopicMastery {
        let parts = row.topic.split(separator: ":", omittingEmptySubsequences: true)
        let sectionID = parts.count >= 2 ? String(parts[1]) : ""
        let studied = row.studiedCards > 0
        let status: MasteryStatus
        if !studied {
            status = .locked
        } else if row.averageRecall >= 0.85 && row.coverage >= 0.8 {
            status = .mastered
        } else if row.averageRecall < 0.55 {
            status = .corrective
        } else {
            status = .active
        }
        return TopicMastery(
            tag: row.topic,
            name: TopicFormat.leaf(row.topic).capitalized,
            section: MCATSection(rawValue: sectionID) ?? .bioBiochem,
            status: status,
            comprehension: studied ? row.averageRecall : nil,
            bandLower: nil,
            bandUpper: nil,
            examWeight: row.weight,
            hasEvidence: studied,
            overconfidence: 0
        )
    }

    /// The three scores, from what the phone can actually defend: memory reads
    /// out of the shared engine's FSRS state; performance and readiness abstain
    /// (their application evidence lives in the den's logs). NO LINE beats a
    /// guess in a nice font — on the phone too.
    static func scores(from rows: [PBTopicMastery], connected: Bool) -> ScoresSnapshot {
        let studied = rows.filter { $0.studiedCards > 0 }
        let studiedCards = studied.reduce(0) { $0 + $1.studiedCards }
        let weightSum = studied.reduce(0.0) { $0 + $1.weight * Double($1.studiedCards) }
        let recall =
            weightSum > 0
            ? studied.reduce(0.0) { $0 + $1.averageRecall * $1.weight * Double($1.studiedCards) } / weightSum
            : nil
        let spread: Double? = {
            guard let recall, studied.count > 1 else { return nil }
            let variance =
                studied.reduce(0.0) { $0 + pow($1.averageRecall - recall, 2) } / Double(studied.count)
            return sqrt(variance)
        }()

        let memory: ScoreReading
        if let recall {
            memory = ScoreReading(
                kind: .memory,
                value: recall,
                lower: max(0, recall - (spread ?? 0.05)),
                upper: min(1, recall + (spread ?? 0.05)),
                abstained: false,
                confidence: studiedCards >= 200 ? "medium" : "low",
                reasons: [
                    "FSRS retrievability across \(studiedCards) studied cards, straight from the shared engine."
                ]
            )
        } else {
            memory = ScoreReading(
                kind: .memory,
                abstained: true,
                reasons: [
                    connected
                        ? "No studied cards on this seat yet — play a hand or sync a den with history."
                        : "This seat hasn't joined a den yet — link your sync server in the Ledger."
                ]
            )
        }

        let performance = ScoreReading(
            kind: .performance,
            abstained: true,
            reasons: [
                "Application hands (quizzes + open-ended) are graded in the den's logs, which don't ride sync yet.",
                "The phone won't dress memory up as transfer.",
            ]
        )
        let readiness = ScoreReading(
            kind: .readiness,
            abstained: true,
            reasons: [
                "The Book posts a line only from graded application evidence — see the desktop's Observatory.",
                "NO LINE — insufficient action on this seat.",
            ]
        )

        let totalWeight = rows.reduce(0.0) { $0 + $1.weight }
        let wonWeight = rows.filter { uiTopic($0).status == .mastered }.reduce(0.0) { $0 + $1.weight }
        let coveredWeight = rows.reduce(0.0) { $0 + $1.weight * $1.coverage }
        return ScoresSnapshot(
            memory: memory,
            performance: performance,
            readiness: readiness,
            overallComprehension: totalWeight > 0 ? wonWeight / totalWeight : nil,
            evidencedFraction: totalWeight > 0 ? coveredWeight / totalWeight : nil,
            selfTrust: nil
        )
    }

    static func uiCard(id: Int64, note: PBNote) -> ReviewCard {
        let topic = note.tags.first { $0.hasPrefix("mcat::") } ?? ""
        let front = note.fields.first ?? ""
        let back = note.fields.count > 1 ? note.fields[1] : ""
        return ReviewCard(
            id: String(id),
            topic: topic,
            question: PlainText.render(front),
            answer: PlainText.render(back)
        )
    }
}

// MARK: - Field text -> screen text

/// Card fields arrive as the HTML Anki stores. The phone renders them as
/// plain text: tags stripped, a few entities decoded, cloze markup reduced to
/// its answer, [sound:...] references dropped.
enum PlainText {
    static func render(_ html: String) -> String {
        var s = html
        s = s.replacingOccurrences(
            of: #"\{\{c\d+::(.*?)(::[^}]*)?\}\}"#, with: "$1", options: .regularExpression)
        s = s.replacingOccurrences(of: #"\[sound:[^\]]*\]"#, with: "", options: .regularExpression)
        s = s.replacingOccurrences(
            of: #"<br\s*/?>|</div>|</p>"#, with: "\n", options: [.regularExpression, .caseInsensitive])
        s = s.replacingOccurrences(of: #"<[^>]+>"#, with: "", options: .regularExpression)
        let entities = [
            "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
            "&quot;": "\"", "&#39;": "'",
        ]
        for (entity, plain) in entities {
            s = s.replacingOccurrences(of: entity, with: plain)
        }
        return s.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
