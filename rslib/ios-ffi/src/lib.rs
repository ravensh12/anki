// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! A minimal C ABI over the Anki backend for the Ante iOS companion.
//!
//! This is the same seam the desktop uses (`pylib/rsbridge`): open a backend,
//! then drive everything through
//! `run_service_method(service, method, bytes) -> bytes`. The Swift side
//! (`ios/Ante/EngineClient.swift`) wraps these calls behind
//! `AnkiServiceBridge`; the (service, method) indices Swift hardcodes are
//! pinned by the tests in this crate.
//!
//! Memory contract:
//! * `ante_backend_open` returns an owned handle; release it with
//!   `ante_backend_close`.
//! * `ante_backend_run` writes an owned buffer into `out`; release it with
//!   `ante_bytes_free` (also on error — the buffer then holds a serialized
//!   `BackendError`).

use std::ffi::c_char;
use std::ffi::CString;
use std::panic::catch_unwind;
use std::panic::AssertUnwindSafe;
use std::slice;
use std::sync::OnceLock;

use anki::backend::init_backend;
use anki::backend::Backend;
use anki::log::set_global_logger;

/// An owned byte buffer passed across the FFI boundary.
#[repr(C)]
pub struct AnteBytes {
    pub data: *mut u8,
    pub len: usize,
    pub cap: usize,
}

impl AnteBytes {
    fn empty() -> Self {
        AnteBytes {
            data: std::ptr::null_mut(),
            len: 0,
            cap: 0,
        }
    }

    fn from_vec(mut v: Vec<u8>) -> Self {
        let out = AnteBytes {
            data: v.as_mut_ptr(),
            len: v.len(),
            cap: v.capacity(),
        };
        std::mem::forget(v);
        out
    }
}

/// Result codes for `ante_backend_run`.
pub const ANTE_OK: i32 = 0;
/// The backend returned an error; `out` holds a serialized
/// `anki.backend.BackendError`.
pub const ANTE_ERR_BACKEND: i32 = 1;
/// Invalid arguments (null pointers) or an internal panic; `out` is empty.
pub const ANTE_ERR_INVALID: i32 = 2;

/// Build hash of the embedded engine, as a static null-terminated C string.
#[no_mangle]
pub extern "C" fn ante_buildhash() -> *const c_char {
    static HASH: OnceLock<CString> = OnceLock::new();
    HASH.get_or_init(|| CString::new(anki::version::buildhash()).unwrap())
        .as_ptr()
}

/// Open a backend from a serialized `anki.backend.BackendInit` message.
/// Returns null if the message fails to decode.
///
/// # Safety
/// `init_bytes` must point to at least `init_len` readable bytes (or be null
/// when `init_len` is 0).
#[no_mangle]
pub unsafe extern "C" fn ante_backend_open(init_bytes: *const u8, init_len: usize) -> *mut Backend {
    if init_bytes.is_null() && init_len > 0 {
        return std::ptr::null_mut();
    }
    let _ = set_global_logger(None);
    let msg = if init_len == 0 {
        &[]
    } else {
        unsafe { slice::from_raw_parts(init_bytes, init_len) }
    };
    match catch_unwind(|| init_backend(msg)) {
        Ok(Ok(backend)) => Box::into_raw(Box::new(backend)),
        _ => std::ptr::null_mut(),
    }
}

/// Run one service method. Writes the response (or a serialized
/// `BackendError`) into `out`; the caller owns it and must call
/// `ante_bytes_free`.
///
/// # Safety
/// `backend` must be a live handle from `ante_backend_open`, `input` must
/// point to `input_len` readable bytes (or be null when `input_len` is 0),
/// and `out` must point to a writable `AnteBytes`.
#[no_mangle]
pub unsafe extern "C" fn ante_backend_run(
    backend: *mut Backend,
    service: u32,
    method: u32,
    input: *const u8,
    input_len: usize,
    out: *mut AnteBytes,
) -> i32 {
    if out.is_null() {
        return ANTE_ERR_INVALID;
    }
    unsafe { out.write(AnteBytes::empty()) };
    if backend.is_null() || (input.is_null() && input_len > 0) {
        return ANTE_ERR_INVALID;
    }
    let backend = unsafe { &*backend };
    let input = if input_len == 0 {
        &[]
    } else {
        unsafe { slice::from_raw_parts(input, input_len) }
    };
    let result = catch_unwind(AssertUnwindSafe(|| {
        backend.run_service_method(service, method, input)
    }));
    match result {
        Ok(Ok(bytes)) => {
            unsafe { out.write(AnteBytes::from_vec(bytes)) };
            ANTE_OK
        }
        Ok(Err(err_bytes)) => {
            unsafe { out.write(AnteBytes::from_vec(err_bytes)) };
            ANTE_ERR_BACKEND
        }
        Err(_) => ANTE_ERR_INVALID,
    }
}

/// Release a buffer produced by `ante_backend_run`.
///
/// # Safety
/// `bytes` must be a buffer returned by `ante_backend_run` (or an all-zero
/// `AnteBytes`), freed exactly once.
#[no_mangle]
pub unsafe extern "C" fn ante_bytes_free(bytes: AnteBytes) {
    if !bytes.data.is_null() {
        unsafe {
            drop(Vec::from_raw_parts(bytes.data, bytes.len, bytes.cap));
        }
    }
}

/// Close a backend opened with `ante_backend_open`.
///
/// # Safety
/// `backend` must be a handle from `ante_backend_open`, closed exactly once
/// (or null).
#[no_mangle]
pub unsafe extern "C" fn ante_backend_close(backend: *mut Backend) {
    if !backend.is_null() {
        unsafe {
            drop(Box::from_raw(backend));
        }
    }
}

#[cfg(test)]
mod tests {
    use anki_proto::backend::BackendInit;
    use anki_proto::collection::OpenCollectionRequest;
    use anki_proto::scheduler::GetQueuedCardsRequest;
    use anki_proto::scheduler::GetTopicMasteryRequest;
    use anki_proto::scheduler::GetTopicMasteryResponse;
    use anki_proto::scheduler::QueuedCards;
    use prost::Message;

    use super::*;

    // The (service, method) indices the Swift bridge hardcodes
    // (ios/Ante/Generated/BackendIndices.swift). These tests exercise them
    // against the real dispatcher, so a proto reordering that shifts any
    // index fails here before it can strand the phone.
    const SYNC_SERVICE: u32 = 1;
    const SYNC_LOGIN: u32 = 3;
    const SYNC_COLLECTION: u32 = 5;
    const FULL_UPLOAD_OR_DOWNLOAD: u32 = 6;
    const COLLECTION_SERVICE: u32 = 3;
    const OPEN_COLLECTION: u32 = 0;
    const CLOSE_COLLECTION: u32 = 1;
    const DECKS_SERVICE: u32 = 7;
    const DECK_TREE: u32 = 4;
    const SET_CURRENT_DECK: u32 = 22;
    const SCHEDULER_SERVICE: u32 = 13;
    const GET_QUEUED_CARDS: u32 = 3;
    const ANSWER_CARD: u32 = 4;
    const GET_TOPIC_MASTERY: u32 = 39;
    const NOTES_SERVICE: u32 = 25;
    const GET_NOTE: u32 = 6;

    fn run(backend: *mut Backend, service: u32, method: u32, input: &[u8]) -> (i32, Vec<u8>) {
        let mut out = AnteBytes::empty();
        let code = unsafe {
            ante_backend_run(
                backend,
                service,
                method,
                input.as_ptr(),
                input.len(),
                &mut out,
            )
        };
        let bytes = if out.data.is_null() {
            Vec::new()
        } else {
            unsafe { slice::from_raw_parts(out.data, out.len) }.to_vec()
        };
        unsafe { ante_bytes_free(out) };
        (code, bytes)
    }

    fn open_test_backend() -> *mut Backend {
        let init = BackendInit {
            preferred_langs: vec!["en".into()],
            ..Default::default()
        }
        .encode_to_vec();
        let backend = unsafe { ante_backend_open(init.as_ptr(), init.len()) };
        assert!(!backend.is_null());
        backend
    }

    #[test]
    fn buildhash_is_a_c_string() {
        let ptr = ante_buildhash();
        assert!(!ptr.is_null());
        let s = unsafe { std::ffi::CStr::from_ptr(ptr) }.to_str().unwrap();
        assert_eq!(s, anki::version::buildhash());
    }

    #[test]
    fn full_round_trip_through_the_c_abi_pins_the_indices() {
        let dir = tempfile::tempdir().unwrap();
        let backend = open_test_backend();

        // open a collection (COLLECTION_SERVICE / OPEN_COLLECTION)
        let req = OpenCollectionRequest {
            collection_path: dir.path().join("test.anki2").to_string_lossy().into_owned(),
            media_folder_path: dir.path().join("media").to_string_lossy().into_owned(),
            media_db_path: dir.path().join("media.db").to_string_lossy().into_owned(),
        }
        .encode_to_vec();
        let (code, _) = run(backend, COLLECTION_SERVICE, OPEN_COLLECTION, &req);
        assert_eq!(code, ANTE_OK);

        // the mastery RPC the Atlas uses (SCHEDULER_SERVICE / GET_TOPIC_MASTERY)
        let req = GetTopicMasteryRequest {
            search: "".into(),
            topic_prefix: "mcat::".into(),
            mastery_threshold: 0.9,
        }
        .encode_to_vec();
        let (code, bytes) = run(backend, SCHEDULER_SERVICE, GET_TOPIC_MASTERY, &req);
        assert_eq!(code, ANTE_OK);
        let resp = GetTopicMasteryResponse::decode(bytes.as_slice()).unwrap();
        assert_eq!(resp.total_cards, 0);

        // the due queue (SCHEDULER_SERVICE / GET_QUEUED_CARDS)
        let req = GetQueuedCardsRequest {
            fetch_limit: 5,
            intraday_learning_only: false,
        }
        .encode_to_vec();
        let (code, bytes) = run(backend, SCHEDULER_SERVICE, GET_QUEUED_CARDS, &req);
        assert_eq!(code, ANTE_OK);
        let queued = QueuedCards::decode(bytes.as_slice()).unwrap();
        assert!(queued.cards.is_empty());

        // deck tree + select-a-deck (DECKS_SERVICE), which the phone uses to
        // seat itself at the deck that holds the cards before dealing
        let req = anki_proto::decks::DeckTreeRequest { now: 0 }.encode_to_vec();
        let (code, bytes) = run(backend, DECKS_SERVICE, DECK_TREE, &req);
        assert_eq!(code, ANTE_OK);
        let tree = anki_proto::decks::DeckTreeNode::decode(bytes.as_slice()).unwrap();
        assert_eq!(tree.children.len(), 1, "fresh collection has just Default");
        let req = anki_proto::decks::DeckId { did: 1 }.encode_to_vec();
        let (code, _) = run(backend, DECKS_SERVICE, SET_CURRENT_DECK, &req);
        assert_eq!(code, ANTE_OK);

        // ANSWER_CARD with an empty card id must be a clean backend error
        // (not a panic), proving the error path serializes a BackendError
        let (code, err_bytes) = run(backend, SCHEDULER_SERVICE, ANSWER_CARD, &[]);
        assert_eq!(code, ANTE_ERR_BACKEND);
        let err = anki_proto::backend::BackendError::decode(err_bytes.as_slice()).unwrap();
        assert!(!err.message.is_empty());

        // GET_NOTE on a missing note is also a clean error
        let req = anki_proto::notes::NoteId { nid: 123 }.encode_to_vec();
        let (code, _) = run(backend, NOTES_SERVICE, GET_NOTE, &req);
        assert_eq!(code, ANTE_ERR_BACKEND);

        // SYNC_LOGIN against an unreachable endpoint fails with a backend
        // error rather than hanging or panicking
        let req = anki_proto::sync::SyncLoginRequest {
            username: "x".into(),
            password: "y".into(),
            endpoint: Some("http://127.0.0.1:1/".into()),
        }
        .encode_to_vec();
        let (code, _) = run(backend, SYNC_SERVICE, SYNC_LOGIN, &req);
        assert_eq!(code, ANTE_ERR_BACKEND);

        // SYNC_COLLECTION and FULL_UPLOAD_OR_DOWNLOAD against an unreachable
        // endpoint must fail with a NETWORK error — proof the indices route to
        // the real sync client (a wrong index would come back INVALID_INPUT)
        let unreachable = anki_proto::sync::SyncAuth {
            hkey: "k".into(),
            endpoint: Some("http://127.0.0.1:1/".into()),
            io_timeout_secs: Some(1),
        };
        let req = anki_proto::sync::SyncCollectionRequest {
            auth: Some(unreachable.clone()),
            sync_media: false,
        }
        .encode_to_vec();
        let (code, err_bytes) = run(backend, SYNC_SERVICE, SYNC_COLLECTION, &req);
        assert_eq!(code, ANTE_ERR_BACKEND);
        let err = anki_proto::backend::BackendError::decode(err_bytes.as_slice()).unwrap();
        assert_eq!(
            err.kind(),
            anki_proto::backend::backend_error::Kind::NetworkError
        );

        let req = anki_proto::sync::FullUploadOrDownloadRequest {
            auth: Some(unreachable),
            upload: false,
            server_usn: None,
        }
        .encode_to_vec();
        let (code, err_bytes) = run(backend, SYNC_SERVICE, FULL_UPLOAD_OR_DOWNLOAD, &req);
        assert_eq!(code, ANTE_ERR_BACKEND);
        let err = anki_proto::backend::BackendError::decode(err_bytes.as_slice()).unwrap();
        assert_eq!(
            err.kind(),
            anki_proto::backend::backend_error::Kind::NetworkError
        );

        // close the collection (COLLECTION_SERVICE / CLOSE_COLLECTION)
        let req = anki_proto::collection::CloseCollectionRequest {
            downgrade_to_schema11: false,
        }
        .encode_to_vec();
        let (code, _) = run(backend, COLLECTION_SERVICE, CLOSE_COLLECTION, &req);
        assert_eq!(code, ANTE_OK);

        unsafe { ante_backend_close(backend) };
    }

    #[test]
    fn invalid_service_is_a_backend_error_not_a_crash() {
        let backend = open_test_backend();
        let (code, _) = run(backend, 9999, 0, &[]);
        assert_eq!(code, ANTE_ERR_BACKEND);
        unsafe { ante_backend_close(backend) };
    }

    #[test]
    fn null_arguments_are_rejected() {
        let mut out = AnteBytes::empty();
        let code =
            unsafe { ante_backend_run(std::ptr::null_mut(), 0, 0, std::ptr::null(), 0, &mut out) };
        assert_eq!(code, ANTE_ERR_INVALID);
        // freeing an empty buffer and closing a null backend are no-ops
        unsafe {
            ante_bytes_free(out);
            ante_backend_close(std::ptr::null_mut());
        }
    }
}
