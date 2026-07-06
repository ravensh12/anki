// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
//
// A minimal protobuf wire-format codec — just enough for the handful of
// backend messages the companion exchanges over `run_service_method`.
//
// Why hand-rolled: the app's spec is "no packages, no external dependencies"
// (project.yml), and the alternative — vendoring SwiftProtobuf + generating
// the full transitive closure of scheduler/cards/notes/sync protos — buys
// nothing for the ~12 fixed-shape messages we touch. Field numbers are pinned
// in BackendMessages.swift right next to their .proto definitions, and the
// (service, method) indices are pinned by tests in rslib/ios-ffi, so drift
// fails loudly on the Rust side before it can strand the phone.
//
// Wire format (proto3): a message is a sequence of (tag, value) pairs where
// tag = (field_number << 3) | wire_type. We use wire types 0 (varint),
// 1 (64-bit), 2 (length-delimited) and 5 (32-bit).

import Foundation

enum ProtoWireType: UInt64 {
    case varint = 0
    case fixed64 = 1
    case lengthDelimited = 2
    case fixed32 = 5
}

enum ProtoWireError: Error, CustomStringConvertible {
    case truncated
    case malformedVarint
    case unsupportedWireType(UInt64)
    case malformedUTF8

    var description: String {
        switch self {
        case .truncated: return "message ended mid-field"
        case .malformedVarint: return "varint ran past 10 bytes"
        case .unsupportedWireType(let t): return "unsupported wire type \(t)"
        case .malformedUTF8: return "string field was not valid UTF-8"
        }
    }
}

// MARK: - Writer

/// Appends proto3-encoded fields to a buffer. Zero/empty scalar values are
/// skipped (proto3 default semantics); embedded messages can be forced onto
/// the wire with `message` for explicit-presence fields.
struct ProtoWriter {
    private(set) var data = Data()

    mutating func appendVarint(_ value: UInt64) {
        var v = value
        while v >= 0x80 {
            data.append(UInt8((v & 0x7F) | 0x80))
            v >>= 7
        }
        data.append(UInt8(v))
    }

    private mutating func appendTag(_ field: Int, _ type: ProtoWireType) {
        appendVarint(UInt64(field) << 3 | type.rawValue)
    }

    /// varint field (uint32/uint64/enum/bool); skipped when zero.
    mutating func uint(_ field: Int, _ value: UInt64) {
        guard value != 0 else { return }
        appendTag(field, .varint)
        appendVarint(value)
    }

    /// int32/int64 field; negative values use the 10-byte two's-complement form.
    mutating func int(_ field: Int, _ value: Int64) {
        guard value != 0 else { return }
        appendTag(field, .varint)
        appendVarint(UInt64(bitPattern: value))
    }

    mutating func bool(_ field: Int, _ value: Bool) {
        uint(field, value ? 1 : 0)
    }

    /// double field; skipped when exactly zero.
    mutating func double(_ field: Int, _ value: Double) {
        guard value != 0 else { return }
        appendTag(field, .fixed64)
        withUnsafeBytes(of: value.bitPattern.littleEndian) { data.append(contentsOf: $0) }
    }

    mutating func string(_ field: Int, _ value: String?) {
        guard let value, !value.isEmpty else { return }
        bytes(field, Data(value.utf8))
    }

    /// length-delimited field (string/bytes/embedded message); skipped when empty.
    mutating func bytes(_ field: Int, _ value: Data) {
        guard !value.isEmpty else { return }
        message(field, value)
    }

    /// embedded message, written even when empty (explicit presence — e.g. a
    /// required `SyncAuth` submessage whose fields are all defaults).
    mutating func message(_ field: Int, _ value: Data) {
        appendTag(field, .lengthDelimited)
        appendVarint(UInt64(value.count))
        data.append(value)
    }
}

// MARK: - Reader

/// One decoded field: number, wire type, and its raw payload.
struct ProtoField {
    var number: Int
    var wireType: ProtoWireType
    /// varint/fixed value for scalar types; zero for length-delimited.
    var scalar: UInt64
    /// raw bytes for length-delimited fields; empty for scalar types.
    var payload: Data

    var uintValue: UInt64 { scalar }
    var intValue: Int64 { Int64(bitPattern: scalar) }
    var boolValue: Bool { scalar != 0 }
    var doubleValue: Double {
        wireType == .fixed64 ? Double(bitPattern: scalar) : Double(scalar)
    }

    func stringValue() throws -> String {
        guard let s = String(data: payload, encoding: .utf8) else {
            throw ProtoWireError.malformedUTF8
        }
        return s
    }
}

/// Decodes a message into its top-level fields (repeated fields appear once
/// per occurrence, in order). Embedded messages stay raw `Data`, so a caller
/// can either decode them further or ship them back verbatim — which is how
/// `SchedulingStates` round-trips through `CardAnswer` without the phone
/// understanding FSRS internals.
struct ProtoReader {
    let fields: [ProtoField]

    init(_ data: Data) throws {
        var fields: [ProtoField] = []
        // Copy to a plain array: Data slices keep their parent's indices.
        let bytes = [UInt8](data)
        var i = 0

        func varint() throws -> UInt64 {
            var result: UInt64 = 0
            var shift: UInt64 = 0
            while true {
                guard i < bytes.count else { throw ProtoWireError.truncated }
                guard shift < 64 else { throw ProtoWireError.malformedVarint }
                let byte = bytes[i]
                i += 1
                result |= UInt64(byte & 0x7F) << shift
                if byte & 0x80 == 0 { return result }
                shift += 7
            }
        }

        while i < bytes.count {
            let tag = try varint()
            let number = Int(tag >> 3)
            guard let type = ProtoWireType(rawValue: tag & 0x7) else {
                throw ProtoWireError.unsupportedWireType(tag & 0x7)
            }
            switch type {
            case .varint:
                fields.append(
                    ProtoField(number: number, wireType: type, scalar: try varint(), payload: Data()))
            case .fixed64:
                guard i + 8 <= bytes.count else { throw ProtoWireError.truncated }
                var v: UInt64 = 0
                for k in 0..<8 { v |= UInt64(bytes[i + k]) << (8 * UInt64(k)) }
                i += 8
                fields.append(ProtoField(number: number, wireType: type, scalar: v, payload: Data()))
            case .fixed32:
                guard i + 4 <= bytes.count else { throw ProtoWireError.truncated }
                var v: UInt64 = 0
                for k in 0..<4 { v |= UInt64(bytes[i + k]) << (8 * UInt64(k)) }
                i += 4
                fields.append(ProtoField(number: number, wireType: type, scalar: v, payload: Data()))
            case .lengthDelimited:
                let len = Int(try varint())
                guard i + len <= bytes.count else { throw ProtoWireError.truncated }
                let payload = Data(bytes[i..<(i + len)])
                i += len
                fields.append(ProtoField(number: number, wireType: type, scalar: 0, payload: payload))
            }
        }
        self.fields = fields
    }

    func first(_ number: Int) -> ProtoField? {
        fields.first { $0.number == number }
    }

    func all(_ number: Int) -> [ProtoField] {
        fields.filter { $0.number == number }
    }

    func string(_ number: Int) throws -> String {
        try first(number)?.stringValue() ?? ""
    }

    func uint(_ number: Int) -> UInt64 {
        first(number)?.uintValue ?? 0
    }

    func int(_ number: Int) -> Int64 {
        first(number)?.intValue ?? 0
    }

    func double(_ number: Int) -> Double {
        first(number)?.doubleValue ?? 0
    }

    func bytes(_ number: Int) -> Data {
        first(number)?.payload ?? Data()
    }

    func strings(_ number: Int) throws -> [String] {
        try all(number).map { try $0.stringValue() }
    }
}
