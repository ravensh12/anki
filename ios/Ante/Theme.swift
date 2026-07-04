// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
//
// The Ante visual identity, ported to SwiftUI. This is the single source of
// truth for the palette, type, spacing, and the handful of chrome components
// (wordmark, tick bar, section header, ruled panel, CTA) every screen reuses.
//
// The look is the den, not app-store bubbly: deep felt green, brass accents,
// ember warnings, cream card faces, flat 1px rules, near-square corners, a
// serif display face, and monospaced uppercase micro-labels. It mirrors the
// desktop den (`ante/web/den.html`) token for token. Always night.

import SwiftUI
import UIKit

// MARK: - Palette (mirrors den.html :root)

private extension Color {
    /// 0xRRGGBB literal -> opaque Color.
    init(rgb: UInt32) {
        self.init(
            red: Double((rgb >> 16) & 0xFF) / 255.0,
            green: Double((rgb >> 8) & 0xFF) / 255.0,
            blue: Double(rgb & 0xFF) / 255.0
        )
    }
}

extension Color {
    /// The felt — the app's ground. `--felt`.
    static let anPaper = Color(rgb: 0x0C1712)
    /// Raised surface. `--panel`.
    static let anPanel = Color(rgb: 0x12211A)
    /// More raised surface. `--panel2`.
    static let anPanel2 = Color(rgb: 0x182B21)
    /// Primary text — warm cream. `--ink`.
    static let anInk = Color(rgb: 0xECE4CD)
    /// Secondary text. `--soft`.
    static let anMuted = Color(rgb: 0xA89F83)
    /// Tertiary text. `--faint`.
    static let anFaint = Color(rgb: 0x6F6A56)
    /// Hairlines. `--rule`.
    static let anRule = Color(rgb: 0x2A3C30)
    /// Strong rules (ink-on-felt). `--rule-strong`.
    static let anRuleStrong = Color(rgb: 0xECE4CD)
    /// Ember — warnings, corrective, the House clawing back. `--ember`.
    static let anSignal = Color(rgb: 0xB5533C)
    /// Brass — the brand accent, chips, the one thing to do. `--brass`.
    static let anBrass = Color(rgb: 0xC9A227)
    /// Won tables, banked days. `--good`.
    static let anGood = Color(rgb: 0x3F8F6B)
    /// Bright gold for chips/streak highlights.
    static let anOchre = Color(rgb: 0xD9A43F)
    /// The cream face of a dealt card. `--card`.
    static let anCard = Color(rgb: 0xF6EFDD)
    /// Ink printed on a dealt card. `--card-ink`.
    static let anCardInk = Color(rgb: 0x221C12)
}

// MARK: - Spacing & metrics

enum AnSpace {
    static let xs: CGFloat = 4
    static let sm: CGFloat = 8
    static let md: CGFloat = 14
    static let lg: CGFloat = 22
    static let xl: CGFloat = 32
    /// Corners stay near-square; 3pt is the whole budget (den uses 3–4px).
    static let radius: CGFloat = 3
}

// MARK: - Type

/// Serif display headings (New York via the system serif design).
private struct AnHeadingModifier: ViewModifier {
    var size: CGFloat
    var weight: Font.Weight
    func body(content: Content) -> some View {
        content
            .font(.system(size: size, weight: weight, design: .serif))
            .foregroundStyle(Color.anInk)
    }
}

/// Monospaced uppercase micro-labels (SF Mono), the app's connective tissue.
private struct AnMicroLabelModifier: ViewModifier {
    var color: Color
    var size: CGFloat
    func body(content: Content) -> some View {
        content
            .font(.system(size: size, weight: .regular, design: .monospaced))
            .textCase(.uppercase)
            .tracking(1.1)
            .foregroundStyle(color)
    }
}

extension View {
    func anHeading(size: CGFloat = 22, weight: Font.Weight = .bold) -> some View {
        modifier(AnHeadingModifier(size: size, weight: weight))
    }

    func anMicroLabel(color: Color = .anMuted, size: CGFloat = 11) -> some View {
        modifier(AnMicroLabelModifier(color: color, size: size))
    }
}

// MARK: - Panels

/// A flat, 1px-ruled, near-square panel — the app's default container.
private struct AnPanelModifier: ViewModifier {
    var fill: Color
    var stroke: Color
    var padding: CGFloat
    func body(content: Content) -> some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(fill)
            .overlay(
                RoundedRectangle(cornerRadius: AnSpace.radius)
                    .strokeBorder(stroke, lineWidth: 1)
            )
    }
}

extension View {
    func anPanel(
        fill: Color = .anPanel2,
        stroke: Color = .anRule,
        padding: CGFloat = 16
    ) -> some View {
        modifier(AnPanelModifier(fill: fill, stroke: stroke, padding: padding))
    }
}

// MARK: - Chrome components

/// The 6pt brass tick that anchors the wordmark and marks live sections.
struct TickBar: View {
    var width: CGFloat = 6
    var height: CGFloat = 28
    var color: Color = .anBrass

    var body: some View {
        Rectangle()
            .fill(color)
            .frame(width: width, height: height)
    }
}

/// The ANTE wordmark: brass tick + letter-spaced serif capitals + the room.
struct Wordmark: View {
    var size: CGFloat = 26
    var room: String = "The Emerald Room"

    var body: some View {
        HStack(spacing: 11) {
            TickBar(width: 6, height: size * 1.05)
            VStack(alignment: .leading, spacing: 2) {
                Text("ANTE")
                    .font(.system(size: size, weight: .heavy, design: .serif))
                    .tracking(size * 0.16)
                    .foregroundStyle(Color.anInk)
                Text(room)
                    .font(.system(size: 9, design: .monospaced))
                    .textCase(.uppercase)
                    .tracking(2.2)
                    .foregroundStyle(Color.anMuted)
            }
        }
    }
}

/// A ruled section header: a mono section glyph, a serif title, a hairline
/// fill, and an optional right-aligned meta label. Mirrors the den `.shead`.
struct SectionHeader: View {
    var number: String
    var title: String
    var meta: String? = nil

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(number)
                .font(.system(size: 11, weight: .regular, design: .monospaced))
                .tracking(1.0)
                .foregroundStyle(Color.anBrass)
            Text(title)
                .font(.system(size: 20, weight: .bold, design: .serif))
                .foregroundStyle(Color.anInk)
            Rectangle()
                .fill(Color.anRule)
                .frame(height: 1)
                .alignmentGuide(.firstTextBaseline) { $0[.bottom] }
            if let meta {
                Text(meta).anMicroLabel(color: .anFaint, size: 10.5)
            }
        }
        .padding(.top, AnSpace.sm)
    }
}

// MARK: - Call to action

enum CTATone {
    case signal
    case good
    case ghost

    var background: Color {
        switch self {
        case .signal: return .anBrass
        case .good: return .anGood
        case .ghost: return .anPanel2
        }
    }

    var foreground: Color {
        switch self {
        case .signal: return .anCardInk
        case .good: return .anCard
        case .ghost: return .anInk
        }
    }
}

/// The single, unmissable primary action — a full-width brass (or calm green)
/// block with a serif lead line and a mono sub-line. One per screen.
struct AnCTAButton: View {
    var lead: String
    var meta: String
    var symbol: String = "play.fill"
    var tone: CTATone = .signal
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(alignment: .center, spacing: AnSpace.md) {
                VStack(alignment: .leading, spacing: AnSpace.xs) {
                    Text(lead)
                        .font(.system(size: 22, weight: .heavy, design: .serif))
                        .foregroundStyle(tone.foreground)
                    Text(meta)
                        .anMicroLabel(color: tone.foreground.opacity(0.85), size: 11)
                }
                Spacer(minLength: AnSpace.md)
                Image(systemName: symbol)
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundStyle(tone.foreground)
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(tone.background)
            .overlay(
                RoundedRectangle(cornerRadius: AnSpace.radius)
                    .strokeBorder(
                        tone == .ghost ? Color.anRuleStrong : .clear,
                        lineWidth: 1
                    )
            )
            .shadow(
                color: tone == .signal ? Color.anBrass.opacity(0.25) : .clear,
                radius: 14
            )
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Model-driven colors
//
// Kept here (not in Models.swift) so the model layer stays UI-free Foundation.

extension MasteryStatus {
    /// The 4px status accent used on Circuit tables and legends.
    var accent: Color {
        switch self {
        case .mastered: return .anGood
        case .active: return .anBrass
        case .corrective: return .anSignal
        case .locked: return .anFaint
        }
    }
}

extension ScoreKind {
    var accent: Color {
        switch self {
        case .memory: return .anInk
        case .performance: return .anOchre
        case .readiness: return .anBrass
        }
    }
}

// MARK: - Reading copy

/// Monospaced running notes/captions — mono, muted, but NOT uppercased (unlike a
/// micro-label). Mirrors the den `.note`.
private struct AnNoteModifier: ViewModifier {
    var color: Color
    var size: CGFloat
    func body(content: Content) -> some View {
        content
            .font(.system(size: size, weight: .regular, design: .monospaced))
            .foregroundStyle(color)
            .lineSpacing(3)
    }
}

extension View {
    func anNote(color: Color = .anMuted, size: CGFloat = 12) -> some View {
        modifier(AnNoteModifier(color: color, size: size))
    }
}

// MARK: - Form controls

/// One option in an `AnSegmented` control.
struct AnSegment<Value: Hashable>: Identifiable {
    var value: Value
    var label: String
    var id: Value { value }
}

/// The den segmented selector: flat, ruled, brass-on-select. Replaces the
/// glassy iOS segmented picker to keep the felt aesthetic.
struct AnSegmented<Value: Hashable>: View {
    var options: [AnSegment<Value>]
    @Binding var selection: Value

    var body: some View {
        HStack(spacing: AnSpace.sm) {
            ForEach(options) { option in
                let isOn = option.value == selection
                Button {
                    selection = option.value
                } label: {
                    Text(option.label)
                        .font(.system(size: 12, weight: .medium, design: .monospaced))
                        .tracking(0.6)
                        .foregroundStyle(isOn ? Color.anCardInk : Color.anInk)
                        .padding(.vertical, 11)
                        .frame(maxWidth: .infinity)
                        .background(isOn ? Color.anBrass : Color.anPanel)
                        .overlay(
                            RoundedRectangle(cornerRadius: AnSpace.radius)
                                .strokeBorder(
                                    isOn ? Color.anBrass : Color.anRule,
                                    lineWidth: 1
                                )
                        )
                }
                .buttonStyle(.plain)
            }
        }
    }
}

/// A labelled switch row: serif title + mono caption + a brass-tinted toggle.
struct AnToggleRow: View {
    var title: String
    var caption: String
    @Binding var isOn: Bool

    var body: some View {
        Toggle(isOn: $isOn) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.system(size: 16, weight: .semibold, design: .serif))
                    .foregroundStyle(Color.anInk)
                Text(caption)
                    .anNote(color: .anMuted, size: 11)
            }
        }
        .tint(.anGood)
    }
}

// MARK: - Data display

/// A big serif number with a mono caption, the app's headline statistic unit.
struct StatBlock: View {
    var value: String
    var caption: String
    var tint: Color = .anInk

    var body: some View {
        VStack(alignment: .leading, spacing: AnSpace.xs) {
            Text(value)
                .font(.system(size: 30, weight: .heavy, design: .serif))
                .foregroundStyle(tint)
                .monospacedDigit()
            Text(caption)
                .anMicroLabel(color: .anMuted, size: 10)
        }
    }
}

/// A thin horizontal fill bar with an optional confidence band overlay.
struct AnMeter: View {
    var fraction: Double
    var bandLower: Double? = nil
    var bandUpper: Double? = nil
    var fill: Color = .anBrass
    var track: Color = .anPanel2
    var height: CGFloat = 8

    var body: some View {
        GeometryReader { geo in
            let width = geo.size.width
            let clamped = max(0, min(1, fraction))
            ZStack(alignment: .leading) {
                Rectangle().fill(track)
                Rectangle()
                    .fill(fill)
                    .frame(width: clamped * width)
                if let lo = bandLower, let hi = bandUpper {
                    let x = max(0, min(1, lo)) * width
                    let bandWidth = max(1, (min(1, hi) - max(0, lo)) * width)
                    Rectangle()
                        .strokeBorder(Color.anMuted, lineWidth: 1)
                        .frame(width: bandWidth)
                        .offset(x: x)
                }
            }
        }
        .frame(height: height)
    }
}

// MARK: - Chips

/// A short stack of brass chips, 1–5, sized by exam weight. Mirrors the den's
/// `.chips` stakes marker.
struct ChipStack: View {
    var count: Int

    var body: some View {
        HStack(spacing: 3) {
            ForEach(0..<max(1, min(5, count)), id: \.self) { _ in
                Circle()
                    .fill(Color.anBrass)
                    .frame(width: 7, height: 7)
                    .overlay(Circle().strokeBorder(Color.anCardInk.opacity(0.5), lineWidth: 1))
            }
        }
    }
}
