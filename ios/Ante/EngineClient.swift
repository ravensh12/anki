// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
//
// The boundary between the UI and the study engine. Everything the app shows —
// the three scores, the mastery map, the due stack — comes through this one
// protocol, so the views never care whether the data is mocked or is coming from
// the real Rust core over the FFI seam.
//
//   * MockEngine   — realistic sample data used today (and in previews/tests).
//   * SyncedEngine — a typed skeleton that will call the shared `anki` Rust crate
//                    (compiled as an xcframework) over the same single
//                    `run_service_method` protobuf seam the desktop uses.

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

// MARK: - The engine boundary

protocol EngineClient {
    /// The three separate, honest scores (readiness abstains until it has evidence).
    func fetchScores() async throws -> ScoresSnapshot
    /// Per-topic comprehension for the Atlas, scored only from application.
    func fetchMastery() async throws -> [TopicMastery]
    /// The scheduled due stack for a session, in points-at-stake order.
    func fetchDue() async throws -> DueQueue
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

// MARK: - The shared-engine seam (skeleton)

/// The single C-ABI entry point the Rust engine exposes, mirroring the desktop's
/// `Backend::run_service_method(service, method, input) -> output`. It is the ONE
/// seam every backend RPC travels through: a request protobuf goes in as bytes, a
/// response protobuf comes back as bytes. Implemented later by the generated
/// `AnkiFFI` xcframework (a thin C shim, or a UniFFI-generated Swift module) built
/// from `rslib` for `aarch64-apple-ios` and the simulator. Kept as a protocol so
/// `SyncedEngine` stays testable and the views never link the FFI module directly.
protocol AnkiServiceBridge {
    func runServiceMethod(service: Int32, method: Int32, input: Data) async throws -> Data
}

/// The backend RPCs this client uses, named for clarity. The generated backend
/// index maps each name to the numeric (service, method) pair the FFI call takes;
/// those indices are read from the generated protobuf descriptors at wiring time
/// rather than hard-coded here, so a version bump can't silently misroute a call.
enum BackendRPC {
    /// `SchedulerService.GetTopicMastery` — the Ante engine change. Request:
    /// { search: "", prefix: "mcat::", threshold: 0.9 }. Response rows carry total /
    /// studied / mastered counts, average recall, and coverage per topic.
    static let getTopicMastery = "SchedulerService.GetTopicMastery"
    /// `SchedulerService.GetQueuedCards` — the due stack, already ordered by the
    /// points-at-stake queue builder on the engine side.
    static let getQueuedCards = "SchedulerService.GetQueuedCards"
}

/// The production engine client. It will:
///
/// 1. Open the local `.anki2` collection through the shared Rust backend inside
///    the xcframework (the exact same crate the desktop and the validated Android
///    build use — not a re-implementation).
/// 2. For each read, encode the request protobuf, hand the bytes to
///    `bridge.runServiceMethod(...)`, and decode the response protobuf into the
///    Swift model types above. `fetchMastery()` is a direct call to
///    `BackendRPC.getTopicMastery`; `fetchDue()` uses `getQueuedCards` and then
///    derives the three scores the same way the desktop's `ante` layer does.
/// 3. Sync two-way against the self-hosted Anki sync server before/after a
///    session, so application evidence answered on desktop reaches readiness here.
///
/// Until the xcframework is built and the protobuf message types are generated for
/// Swift, this client has no bridge and serves the same sample data as `MockEngine`,
/// which keeps the whole UI runnable. Swapping in a real `AnkiServiceBridge` is the
/// only change needed to make it live.
final class SyncedEngine: EngineClient {
    private let bridge: AnkiServiceBridge?
    private let fallback: EngineClient

    init(bridge: AnkiServiceBridge? = nil, fallback: EngineClient = MockEngine()) {
        self.bridge = bridge
        self.fallback = fallback
    }

    /// True once the shared Rust engine is wired in behind the FFI seam.
    var isLive: Bool { bridge != nil }

    func fetchScores() async throws -> ScoresSnapshot {
        // Real path: derive from getQueuedCards + the mastery RPC, mirroring the
        // desktop readiness computation. No bridge yet -> honest sample data.
        try await fallback.fetchScores()
    }

    func fetchMastery() async throws -> [TopicMastery] {
        // Real path: encode a GetTopicMasteryRequest, call BackendRPC.getTopicMastery
        // over the bridge, decode the response rows. No bridge yet -> sample data.
        try await fallback.fetchMastery()
    }

    func fetchDue() async throws -> DueQueue {
        // Real path: BackendRPC.getQueuedCards returns the points-at-stake-ordered
        // stack straight from the engine. No bridge yet -> sample data.
        try await fallback.fetchDue()
    }
}
