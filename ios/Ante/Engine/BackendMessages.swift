// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
//
// Typed encode/decode for the backend messages the companion uses, with the
// field numbers written next to their .proto definitions. Everything here is
// a plain value type over ProtoWire; no Anki semantics beyond field layout.

import Foundation

// MARK: - anki.backend

/// backend.proto: BackendInit { preferred_langs=1, locale_folder_path=2, server=3 }
enum PBBackendInit {
    static func encode(preferredLangs: [String] = ["en"]) -> Data {
        var w = ProtoWriter()
        for lang in preferredLangs { w.string(1, lang) }
        return w.data
    }
}

/// backend.proto: BackendError { message=1, kind=2, context=4 }
struct PBBackendError: Error, CustomStringConvertible {
    var message: String
    var kind: UInt64
    var context: String

    static func decode(_ data: Data) -> PBBackendError {
        guard let r = try? ProtoReader(data) else {
            return PBBackendError(message: "undecodable backend error", kind: 0, context: "")
        }
        return PBBackendError(
            message: (try? r.string(1)) ?? "",
            kind: r.uint(2),
            context: (try? r.string(4)) ?? ""
        )
    }

    var description: String {
        message.isEmpty ? "backend error (kind \(kind))" : message
    }
}

// MARK: - anki.collection

/// collection.proto: OpenCollectionRequest { collection_path=1,
/// media_folder_path=2, media_db_path=3 }
enum PBOpenCollectionRequest {
    static func encode(collectionPath: String, mediaFolderPath: String, mediaDBPath: String) -> Data {
        var w = ProtoWriter()
        w.string(1, collectionPath)
        w.string(2, mediaFolderPath)
        w.string(3, mediaDBPath)
        return w.data
    }
}

/// collection.proto: CloseCollectionRequest { downgrade_to_schema11=1 }
enum PBCloseCollectionRequest {
    static func encode(downgrade: Bool = false) -> Data {
        var w = ProtoWriter()
        w.bool(1, downgrade)
        return w.data
    }
}

// MARK: - anki.sync

/// sync.proto: SyncAuth { hkey=1, endpoint=2, io_timeout_secs=3 }
struct PBSyncAuth: Codable, Equatable {
    var hkey: String
    var endpoint: String

    func encode() -> Data {
        var w = ProtoWriter()
        w.string(1, hkey)
        w.string(2, endpoint)
        return w.data
    }

    static func decode(_ data: Data) throws -> PBSyncAuth {
        let r = try ProtoReader(data)
        return PBSyncAuth(hkey: try r.string(1), endpoint: try r.string(2))
    }
}

/// sync.proto: SyncLoginRequest { username=1, password=2, endpoint=3 }
enum PBSyncLoginRequest {
    static func encode(username: String, password: String, endpoint: String) -> Data {
        var w = ProtoWriter()
        w.string(1, username)
        w.string(2, password)
        w.string(3, endpoint)
        return w.data
    }
}

/// sync.proto: SyncCollectionRequest { auth=1, sync_media=2 }
enum PBSyncCollectionRequest {
    static func encode(auth: PBSyncAuth, syncMedia: Bool = false) -> Data {
        var w = ProtoWriter()
        w.message(1, auth.encode())
        w.bool(2, syncMedia)
        return w.data
    }
}

/// sync.proto: SyncCollectionResponse.ChangesRequired
enum PBChangesRequired: UInt64 {
    case noChanges = 0
    case normalSync = 1
    case fullSync = 2
    case fullDownload = 3
    case fullUpload = 4
}

/// sync.proto: SyncCollectionResponse { host_number=1, server_message=2,
/// required=3, new_endpoint=4, server_media_usn=5 }
struct PBSyncCollectionResponse {
    var serverMessage: String
    var required: PBChangesRequired
    var newEndpoint: String

    static func decode(_ data: Data) throws -> PBSyncCollectionResponse {
        let r = try ProtoReader(data)
        return PBSyncCollectionResponse(
            serverMessage: try r.string(2),
            required: PBChangesRequired(rawValue: r.uint(3)) ?? .noChanges,
            newEndpoint: try r.string(4)
        )
    }
}

/// sync.proto: FullUploadOrDownloadRequest { auth=1, upload=2, server_usn=3 }
/// `server_usn` is deliberately omitted: "if not provided, media syncing will
/// be skipped" — the companion syncs the collection only.
enum PBFullUploadOrDownloadRequest {
    static func encode(auth: PBSyncAuth, upload: Bool) -> Data {
        var w = ProtoWriter()
        w.message(1, auth.encode())
        w.bool(2, upload)
        return w.data
    }
}

// MARK: - anki.decks

/// decks.proto: DeckId { did=1 }
enum PBDeckID {
    static func encode(_ did: Int64) -> Data {
        var w = ProtoWriter()
        w.int(1, did)
        return w.data
    }
}

/// decks.proto: DeckTreeRequest { now=1 }
enum PBDeckTreeRequest {
    static func encode(now: Int64) -> Data {
        var w = ProtoWriter()
        w.int(1, now)
        return w.data
    }
}

/// decks.proto: DeckTreeNode { deck_id=1, name=2, children=3,
/// total_including_children=14 } — the slice the phone needs to find the deck
/// that actually holds the cards (mirrors the desktop's `_ante_pick_deck`).
struct PBDeckTreeNode {
    var deckID: Int64
    var name: String
    var children: [PBDeckTreeNode]
    var totalIncludingChildren: Int

    static func decode(_ data: Data) throws -> PBDeckTreeNode {
        let r = try ProtoReader(data)
        return PBDeckTreeNode(
            deckID: r.int(1),
            name: try r.string(2),
            children: try r.all(3).map { try PBDeckTreeNode.decode($0.payload) },
            totalIncludingChildren: Int(r.uint(14))
        )
    }
}

// MARK: - anki.scheduler (incl. the Ante engine change)

/// scheduler.proto: GetTopicMasteryRequest { search=1, topic_prefix=2,
/// mastery_threshold=3 }
enum PBGetTopicMasteryRequest {
    static func encode(search: String = "", topicPrefix: String = "mcat::", threshold: Double = 0.9) -> Data {
        var w = ProtoWriter()
        w.string(1, search)
        w.string(2, topicPrefix)
        w.double(3, threshold)
        return w.data
    }
}

/// scheduler.proto: TopicMastery { topic=1, weight=2, total_cards=3,
/// studied_cards=4, mastered_cards=5, average_recall=6, coverage=7 }
struct PBTopicMastery {
    var topic: String
    var weight: Double
    var totalCards: Int
    var studiedCards: Int
    var masteredCards: Int
    var averageRecall: Double
    var coverage: Double

    static func decode(_ data: Data) throws -> PBTopicMastery {
        let r = try ProtoReader(data)
        return PBTopicMastery(
            topic: try r.string(1),
            weight: r.double(2),
            totalCards: Int(r.uint(3)),
            studiedCards: Int(r.uint(4)),
            masteredCards: Int(r.uint(5)),
            averageRecall: r.double(6),
            coverage: r.double(7)
        )
    }
}

/// scheduler.proto: GetTopicMasteryResponse { topics=1, topic_count=2, total_cards=3 }
struct PBGetTopicMasteryResponse {
    var topics: [PBTopicMastery]
    var totalCards: Int

    static func decode(_ data: Data) throws -> PBGetTopicMasteryResponse {
        let r = try ProtoReader(data)
        return PBGetTopicMasteryResponse(
            topics: try r.all(1).map { try PBTopicMastery.decode($0.payload) },
            totalCards: Int(r.uint(3))
        )
    }
}

/// scheduler.proto: GetQueuedCardsRequest { fetch_limit=1, intraday_learning_only=2 }
enum PBGetQueuedCardsRequest {
    static func encode(fetchLimit: UInt32, intradayLearningOnly: Bool = false) -> Data {
        var w = ProtoWriter()
        w.uint(1, UInt64(fetchLimit))
        w.bool(2, intradayLearningOnly)
        return w.data
    }
}

/// cards.proto: Card { id=1, note_id=2, deck_id=3, ... } — only the ids the
/// companion needs; the rest of the row stays with the engine.
struct PBCardIds {
    var id: Int64
    var noteID: Int64

    static func decode(_ data: Data) throws -> PBCardIds {
        let r = try ProtoReader(data)
        return PBCardIds(id: r.int(1), noteID: r.int(2))
    }
}

/// scheduler.proto: QueuedCards.QueuedCard { card=1, queue=2, states=3 }.
/// `states` (SchedulingStates { current=1, again=2, hard=3, good=4, easy=5 })
/// is kept as raw bytes and echoed back through CardAnswer, so the phone
/// never re-implements — or risks disagreeing with — the FSRS state machine.
struct PBQueuedCard {
    var card: PBCardIds
    var queue: UInt64
    var statesCurrent: Data
    var statesAgain: Data
    var statesHard: Data
    var statesGood: Data
    var statesEasy: Data

    static func decode(_ data: Data) throws -> PBQueuedCard {
        let r = try ProtoReader(data)
        let states = try ProtoReader(r.bytes(3))
        return PBQueuedCard(
            card: try PBCardIds.decode(r.bytes(1)),
            queue: r.uint(2),
            statesCurrent: states.bytes(1),
            statesAgain: states.bytes(2),
            statesHard: states.bytes(3),
            statesGood: states.bytes(4),
            statesEasy: states.bytes(5)
        )
    }

    func newState(for rating: PBRating) -> Data {
        switch rating {
        case .again: return statesAgain
        case .hard: return statesHard
        case .good: return statesGood
        case .easy: return statesEasy
        }
    }
}

/// scheduler.proto: QueuedCards { cards=1, new_count=2, learning_count=3, review_count=4 }
struct PBQueuedCards {
    var cards: [PBQueuedCard]
    var newCount: Int
    var learningCount: Int
    var reviewCount: Int

    var totalDue: Int { newCount + learningCount + reviewCount }

    static func decode(_ data: Data) throws -> PBQueuedCards {
        let r = try ProtoReader(data)
        return PBQueuedCards(
            cards: try r.all(1).map { try PBQueuedCard.decode($0.payload) },
            newCount: Int(r.uint(2)),
            learningCount: Int(r.uint(3)),
            reviewCount: Int(r.uint(4))
        )
    }
}

/// scheduler.proto: CardAnswer.Rating
enum PBRating: UInt64 {
    case again = 0
    case hard = 1
    case good = 2
    case easy = 3
}

/// scheduler.proto: CardAnswer { card_id=1, current_state=2, new_state=3,
/// rating=4, answered_at_millis=5, milliseconds_taken=6 }
enum PBCardAnswer {
    static func encode(
        cardID: Int64,
        currentState: Data,
        newState: Data,
        rating: PBRating,
        answeredAtMillis: Int64,
        millisecondsTaken: UInt32
    ) -> Data {
        var w = ProtoWriter()
        w.int(1, cardID)
        w.message(2, currentState)
        w.message(3, newState)
        w.uint(4, rating.rawValue)
        w.int(5, answeredAtMillis)
        w.uint(6, UInt64(millisecondsTaken))
        return w.data
    }
}

// MARK: - anki.notes

/// notes.proto: NoteId { nid=1 }
enum PBNoteID {
    static func encode(_ nid: Int64) -> Data {
        var w = ProtoWriter()
        w.int(1, nid)
        return w.data
    }
}

/// notes.proto: Note { id=1, guid=2, notetype_id=3, mtime_secs=4, usn=5,
/// tags=6, fields=7 }
struct PBNote {
    var id: Int64
    var tags: [String]
    var fields: [String]

    static func decode(_ data: Data) throws -> PBNote {
        let r = try ProtoReader(data)
        return PBNote(id: r.int(1), tags: try r.strings(6), fields: try r.strings(7))
    }
}
