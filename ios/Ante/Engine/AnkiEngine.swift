// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
//
// The Swift face of the shared Rust engine. `AnkiEngine` owns one backend
// handle from AnkiEngine.xcframework (rslib/ios-ffi) and funnels every RPC
// through the same single seam the desktop uses:
// `run_service_method(service, method, bytes) -> bytes`.
//
// All C calls are blocking, so they run on a private serial queue — one
// engine, one call at a time, never on the Swift-concurrency thread pool.

import Foundation

enum EngineError: LocalizedError {
    /// The backend failed to initialize (should not happen in practice).
    case backendUnavailable
    /// Null-pointer/panic guard tripped inside the FFI layer.
    case invalidCall
    /// A real backend error, decoded from the serialized BackendError.
    case backend(PBBackendError)

    var errorDescription: String? {
        switch self {
        case .backendUnavailable: return "The engine could not be started."
        case .invalidCall: return "The engine rejected the call."
        case .backend(let err): return err.description
        }
    }
}

/// @unchecked Sendable: the only state is an immutable backend handle plus a
/// serial queue, and every FFI call is funneled through that queue.
final class AnkiEngine: @unchecked Sendable {
    private let queue = DispatchQueue(label: "app.ante.engine", qos: .userInitiated)
    private let backend: OpaquePointer

    /// Result codes from anki_engine.h (kept local: anonymous C enum
    /// constants import awkwardly across toolchains).
    private static let okCode: Int32 = 0
    private static let backendErrCode: Int32 = 1

    init() throws {
        let initBytes = PBBackendInit.encode()
        let handle = initBytes.withUnsafeBytes { buf -> OpaquePointer? in
            ante_backend_open(buf.bindMemory(to: UInt8.self).baseAddress, buf.count)
        }
        guard let handle else { throw EngineError.backendUnavailable }
        backend = handle
    }

    deinit {
        ante_backend_close(backend)
    }

    /// Build hash of the embedded engine — proof on a screen that the phone
    /// runs the same modified crate as the desktop.
    static var buildHash: String {
        String(cString: ante_buildhash())
    }

    // MARK: Calls

    /// Run one RPC on the engine queue (blocking; call via `run` from async code).
    private func runBlocking(_ m: BackendMethod, _ input: Data) throws -> Data {
        var out = AnteBytes()
        let code = input.withUnsafeBytes { buf -> Int32 in
            ante_backend_run(
                backend,
                m.service,
                m.method,
                buf.bindMemory(to: UInt8.self).baseAddress,
                buf.count,
                &out
            )
        }
        defer { ante_bytes_free(out) }
        let bytes = out.data.map { Data(bytes: $0, count: out.len) } ?? Data()
        switch code {
        case Self.okCode:
            return bytes
        case Self.backendErrCode:
            throw EngineError.backend(PBBackendError.decode(bytes))
        default:
            throw EngineError.invalidCall
        }
    }

    func run(_ m: BackendMethod, _ input: Data = Data()) async throws -> Data {
        try await withCheckedThrowingContinuation { continuation in
            queue.async {
                do {
                    continuation.resume(returning: try self.runBlocking(m, input))
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    // MARK: Collection lifecycle

    /// Where the phone's collection lives (Documents/AnteCollection).
    /// ANTE_COLLECTION_DIR overrides it for host-side smoke tests.
    static func collectionDirectory() throws -> URL {
        if let override = ProcessInfo.processInfo.environment["ANTE_COLLECTION_DIR"],
            !override.isEmpty
        {
            let dir = URL(fileURLWithPath: override, isDirectory: true)
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            return dir
        }
        let docs = try FileManager.default.url(
            for: .documentDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        let dir = docs.appendingPathComponent("AnteCollection", isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    func openCollection(in dir: URL) async throws {
        let request = PBOpenCollectionRequest.encode(
            collectionPath: dir.appendingPathComponent("collection.anki2").path,
            mediaFolderPath: dir.appendingPathComponent("media", isDirectory: true).path,
            mediaDBPath: dir.appendingPathComponent("media.db").path
        )
        _ = try await run(BackendIndices.openCollection, request)
    }

    /// Close is best-effort: "collection not open" is not a failure here.
    func closeCollection() async {
        _ = try? await run(BackendIndices.closeCollection, PBCloseCollectionRequest.encode())
    }
}
