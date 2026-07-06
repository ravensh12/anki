// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
//
// The numeric (service, method) indices for `run_service_method`, matching
// the dispatcher generated from the .proto service definitions. These are the
// values the desktop's pylib bridge uses; they are PINNED by the Rust tests in
// `rslib/ios-ffi/src/lib.rs` (`full_round_trip_through_the_c_abi_pins_the_indices`),
// which exercise each index against the real dispatcher — a proto reordering
// that shifts any of them fails `cargo test -p anki_ios_ffi` before it can
// strand the phone.

import Foundation

/// One backend RPC: its service index and its method index within the service.
struct BackendMethod {
    var service: UInt32
    var method: UInt32
}

enum BackendIndices {
    // anki.sync.BackendSyncService (service 1); method order follows sync.proto:
    // SyncMedia=0, AbortMediaSync=1, MediaSyncStatus=2, SyncLogin=3,
    // SyncStatus=4, SyncCollection=5, FullUploadOrDownload=6, AbortSync=7.
    static let syncLogin = BackendMethod(service: 1, method: 3)
    static let syncCollection = BackendMethod(service: 1, method: 5)
    static let fullUploadOrDownload = BackendMethod(service: 1, method: 6)

    // anki.collection.CollectionService (service 3).
    static let openCollection = BackendMethod(service: 3, method: 0)
    static let closeCollection = BackendMethod(service: 3, method: 1)

    // anki.decks.DecksService (service 7); order follows decks.proto:
    // NewDeck=0 … DeckTree=4 … SetCurrentDeck=22, GetCurrentDeck=23.
    static let deckTree = BackendMethod(service: 7, method: 4)
    static let setCurrentDeck = BackendMethod(service: 7, method: 22)

    // anki.scheduler.SchedulerService (service 13).
    static let getQueuedCards = BackendMethod(service: 13, method: 3)
    static let answerCard = BackendMethod(service: 13, method: 4)
    /// The Ante engine change: per-topic mastery rollup.
    static let getTopicMastery = BackendMethod(service: 13, method: 39)

    // anki.notes.NotesService (service 25).
    static let getNote = BackendMethod(service: 25, method: 6)
}
