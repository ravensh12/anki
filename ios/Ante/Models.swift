// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
//
// The data the companion personalizes on and renders. These types mirror the
// desktop's `ante/profile.py`, `ante/recalibrate.py`, `ante/reminders.py`, and
// `ante/circuit.py` so the same vocabulary — exam date, chronotype, windows,
// slot roles, cities and tables, no-shame reminder copy — flows from onboarding
// to the Ledger to the lock-screen notification. Everything here is pure
// Foundation: no SwiftUI, no engine calls, unit-testable in isolation.

import Foundation

// MARK: - Tunable constants (mirror ante/config.py)

enum AnConfig {
    /// The run: consecutive nights of real play that earn the gift card.
    static let runTargetNights = 30

    static let defaultDailyMinutes = 75
    static let minDailyMinutes = 15
    static let maxDailyMinutes = 300

    static let retentionFloor = 0.85
    static let retentionCeiling = 0.94
    static let retentionRampDays = 60

    static let secondsPerCard = 8.0
    static let studyDaysPerWeek = 6.0

    static let quietStartHour = 22
    static let quietEndHour = 7

    static let scaleMin = 472
    static let scaleMax = 528
    static let sectionMin = 118
    static let sectionMax = 132

    /// An application item is interleaved every Nth step of a session.
    static let interleaveEvery = 4
}

// MARK: - Enumerations

enum MCATSection: String, CaseIterable, Codable, Identifiable {
    case bioBiochem = "bio_biochem"
    case chemPhys = "chem_phys"
    case psychSoc = "psych_soc"
    case cars

    var id: String { rawValue }

    var code: String {
        switch self {
        case .bioBiochem: return "B/B"
        case .chemPhys: return "C/P"
        case .psychSoc: return "P/S"
        case .cars: return "CARS"
        }
    }

    /// Short label used in section headers.
    var title: String {
        switch self {
        case .bioBiochem: return "Bio / Biochem"
        case .chemPhys: return "Chem / Phys"
        case .psychSoc: return "Psych / Soc"
        case .cars: return "CARS"
        }
    }

    var fullName: String {
        switch self {
        case .bioBiochem: return "Biological & Biochemical Foundations"
        case .chemPhys: return "Chemical & Physical Foundations"
        case .psychSoc: return "Psychological, Social & Biological Foundations"
        case .cars: return "Critical Analysis & Reasoning"
        }
    }

    var tagPrefix: String { "mcat::\(rawValue)::" }

    // The Circuit's world tour (mirrors ante/circuit.py CITY_ORDER/CITY_FLAVOR).

    /// The tour's stop order: home den first, then out into the world.
    static let tourOrder: [MCATSection] = [.chemPhys, .cars, .bioBiochem, .psychSoc]

    /// The city this section plays in.
    var city: String {
        switch self {
        case .chemPhys: return "New York"
        case .cars: return "Monte Carlo"
        case .bioBiochem: return "Havana"
        case .psychSoc: return "Macau"
        }
    }

    /// The room's name inside that city.
    var room: String {
        switch self {
        case .chemPhys: return "The Emerald Room"
        case .cars: return "Salon Bleu"
        case .bioBiochem: return "Casa Verde"
        case .psychSoc: return "The Jade House"
        }
    }

    /// The room's flavor line.
    var flavor: String {
        switch self {
        case .chemPhys: return "brass lamps, rain on glass, and clean arithmetic"
        case .cars: return "read the table, not the cards"
        case .bioBiochem: return "living systems and warm night air"
        case .psychSoc: return "people, and the reasons they play"
        }
    }
}

enum Chronotype: String, CaseIterable, Codable, Identifiable {
    case lark
    case neutral
    case owl

    var id: String { rawValue }

    var label: String {
        switch self {
        case .lark: return "Morning lark"
        case .neutral: return "In between"
        case .owl: return "Night owl"
        }
    }
}

enum StudyWindow: String, CaseIterable, Codable, Identifiable {
    case morning
    case duringTheDay = "during the day"
    case night

    var id: String { rawValue }

    var label: String {
        switch self {
        case .morning: return "Morning"
        case .duringTheDay: return "During the day"
        case .night: return "Night"
        }
    }

    /// The clock hour this window's notification fires at (mirrors WINDOW_HOURS).
    var hour: Int {
        switch self {
        case .morning: return 8
        case .duringTheDay: return 14
        case .night: return 21
        }
    }
}

enum MasteryStatus: String, CaseIterable, Codable, Identifiable {
    case mastered
    case active
    case corrective
    case locked

    var id: String { rawValue }
    var label: String { rawValue }

    /// The Circuit's table-state label (mirrors ante/circuit.py TABLE_*).
    var tableLabel: String {
        switch self {
        case .mastered: return "won"
        case .active: return "open"
        case .corrective: return "low table"
        case .locked: return "roped off"
        }
    }
}

enum ScoreKind: String, CaseIterable, Codable, Identifiable {
    case memory
    case performance
    case readiness

    var id: String { rawValue }

    var title: String {
        switch self {
        case .memory: return "Memory"
        case .performance: return "Performance"
        case .readiness: return "Readiness"
        }
    }

    var question: String {
        switch self {
        case .memory: return "Can you recall it now?"
        case .performance: return "Can you use it on a new question?"
        case .readiness: return "What would you score today?"
        }
    }
}

/// The learning-science role a study window plays (mirrors recalibrate roles).
enum SlotRole: String, Codable {
    case new
    case review
    case encode

    /// Map a slot role to the notification kind it fires (mirrors _ROLE_KIND).
    var notificationKind: NotificationKind {
        switch self {
        case .new: return .retrieval
        case .review: return .review
        case .encode: return .encode
        }
    }
}

enum NotificationKind: String, Codable {
    case retrieval
    case review
    case encode
}

/// The two daily bookends the Today ritual tracks.
enum RitualWindow: String, Codable {
    case morning
    case night
}

// MARK: - Topic tag formatting (mirrors dashboard nice()/leaf())

enum TopicFormat {
    static func nice(_ tag: String) -> String {
        tag.replacingOccurrences(of: "mcat::", with: "")
            .replacingOccurrences(of: "::", with: " · ")
            .replacingOccurrences(of: "_", with: " ")
    }

    static func leaf(_ tag: String) -> String {
        let pretty = nice(tag)
        return pretty.components(separatedBy: " · ").last ?? pretty
    }
}

// MARK: - Study profile (mirrors ante/profile.py)

struct StudyProfile: Codable, Equatable {
    var examDate: Date?
    var targetScore: Int?
    var dailyMinutes: Int
    var studyWindows: [StudyWindow]
    var chronotype: Chronotype
    var remindersEnabled: Bool
    var quietStartHour: Int
    var quietEndHour: Int
    var rewardsOptIn: Bool
    var onboarded: Bool

    static let `default` = StudyProfile(
        examDate: nil,
        targetScore: nil,
        dailyMinutes: AnConfig.defaultDailyMinutes,
        studyWindows: [.morning, .duringTheDay, .night],
        chronotype: .neutral,
        remindersEnabled: true,
        quietStartHour: AnConfig.quietStartHour,
        quietEndHour: AnConfig.quietEndHour,
        rewardsOptIn: false,
        onboarded: false
    )

    /// True if `hour` (0..23) is inside the protected sleep/quiet window.
    /// Mirrors `StudyProfile.in_quiet_hours` including the midnight wrap.
    func inQuietHours(_ hour: Int) -> Bool {
        let s = ((quietStartHour % 24) + 24) % 24
        let e = ((quietEndHour % 24) + 24) % 24
        if s == e { return false }
        if s < e { return (s..<e).contains(hour) }
        return hour >= s || hour < e
    }

    /// Whole days from the start of today to the start of exam day.
    var daysRemaining: Int? {
        guard let examDate else { return nil }
        let cal = Calendar.current
        let start = cal.startOfDay(for: Date())
        let end = cal.startOfDay(for: examDate)
        return cal.dateComponents([.day], from: start, to: end).day
    }
}

// MARK: - Scores

struct ScoreReading: Codable, Identifiable {
    var kind: ScoreKind
    /// Fraction 0...1 for memory/performance; a 472...528 total for readiness.
    var value: Double?
    var lower: Double?
    var upper: Double?
    var abstained: Bool
    var confidence: String?
    var reasons: [String]

    var id: String { kind.rawValue }
    var hasRange: Bool { lower != nil && upper != nil }

    init(
        kind: ScoreKind,
        value: Double? = nil,
        lower: Double? = nil,
        upper: Double? = nil,
        abstained: Bool = false,
        confidence: String? = nil,
        reasons: [String] = []
    ) {
        self.kind = kind
        self.value = value
        self.lower = lower
        self.upper = upper
        self.abstained = abstained
        self.confidence = confidence
        self.reasons = reasons
    }
}

struct ScoresSnapshot: Codable {
    var memory: ScoreReading
    var performance: ScoreReading
    var readiness: ScoreReading
    var overallComprehension: Double?
    var evidencedFraction: Double?
    var selfTrust: Int?
}

// MARK: - Mastery

struct TopicMastery: Codable, Identifiable {
    var tag: String
    var name: String
    var section: MCATSection
    var status: MasteryStatus
    /// Comprehension 0...1, or nil when there is no application evidence yet.
    var comprehension: Double?
    var bandLower: Double?
    var bandUpper: Double?
    var examWeight: Double
    var hasEvidence: Bool
    /// 0...1 downward pull applied where the student is overconfident.
    var overconfidence: Double

    var id: String { tag }
    var displayName: String { name.isEmpty ? TopicFormat.leaf(tag) : name }
}

// MARK: - Ritual state (the daily bookends + streak)

struct RitualState: Codable {
    var morningDone: Bool
    var nightDone: Bool
    var streak: Int
    var lastCompletedDay: Date?

    static let empty = RitualState(
        morningDone: false,
        nightDone: false,
        streak: 0,
        lastCompletedDay: nil
    )
}

// MARK: - Session items

struct ReviewCard: Codable, Identifiable {
    var id: String
    var topic: String
    var question: String
    var answer: String
}

struct ApplicationItem: Codable, Identifiable {
    var id: String
    var topic: String
    var stem: String
    var choices: [String]
    var correctIndex: Int
    var isRetest: Bool
}

// MARK: - Reminders (mirrors ante/reminders.py)

struct Reminder: Identifiable, Equatable {
    var hour: Int
    var minute: Int
    var window: StudyWindow
    var kind: NotificationKind
    var title: String
    var body: String

    var id: String { "\(window.rawValue)-\(hour):\(minute)" }
    var minutesOfDay: Int { hour * 60 + minute }
    var clock: String { String(format: "%02d:%02d", hour, minute) }
}

enum ReminderBuilder {
    private static func cardTarget(minutes: Int, secondsPerCard: Double) -> Int {
        max(1, Int(Double(minutes) * 60.0 / max(1.0, secondsPerCard)))
    }

    /// The day's reminder schedule (empty if reminders are off). Mirrors
    /// `reminders.build_schedule`: quiet-hour windows are dropped, output is
    /// ordered by clock time.
    static func schedule(
        profile: StudyProfile,
        slotPlan: [SlotPlanRow],
        dueCount: Int = 0,
        bestNextTopic: String? = nil,
        daysRemaining: Int? = nil,
        secondsPerCard: Double = AnConfig.secondsPerCard
    ) -> [Reminder] {
        guard profile.remindersEnabled else { return [] }
        var out: [Reminder] = []
        for slot in slotPlan {
            let minutes = slot.minutes
            guard minutes > 0 else { continue }
            let hour = slot.window.hour
            if profile.inQuietHours(hour) { continue }
            let kind = slot.role.notificationKind
            let cards = cardTarget(minutes: minutes, secondsPerCard: secondsPerCard)
            let (title, body) = copy(
                kind: kind,
                cards: cards,
                minutes: minutes,
                dueCount: dueCount,
                daysRemaining: daysRemaining,
                bestNextTopic: bestNextTopic
            )
            out.append(
                Reminder(
                    hour: hour,
                    minute: 0,
                    window: slot.window,
                    kind: kind,
                    title: title,
                    body: body
                )
            )
        }
        return out.sorted { $0.minutesOfDay < $1.minutesOfDay }
    }

    /// The no-shame, cue-anchored copy for one reminder. Mirrors `ante/reminders._copy`.
    static func copy(
        kind: NotificationKind,
        cards: Int,
        minutes: Int,
        dueCount: Int,
        daysRemaining: Int?,
        bestNextTopic: String?
    ) -> (title: String, body: String) {
        let topic = TopicFormat.nice(bestNextTopic ?? "")
        let ahead = dueCount == 0
        let n = ahead ? cards : min(cards, dueCount)

        var title: String
        var body: String
        switch kind {
        case .retrieval:
            title = "The morning game opens"
            body = ahead
                ? "Nothing due — a ~\(minutes) min warm-up hand keeps you loose. No pressure."
                : "~\(n) cards on the felt (~\(minutes) min). Cold recall beats warm rereading — the deck is already stacked in your favor."
        case .encode:
            title = "Last hand before lights out"
            body = ahead
                ? "Optional midnight hand (~\(minutes) min) — light, then lights out."
                : "~\(n) cards (~\(minutes) min). Play them now and your brain banks them overnight — the cheapest minutes of the day."
        case .review:
            title = "Midday — protect your stack"
            body = ahead
                ? "You're clear for now. Rest is part of the schedule."
                : "~\(n) cards (~\(minutes) min) are ripe. A few minutes now and the House doesn't claw them back."
        }

        if !topic.isEmpty && !ahead {
            body += " First card up: \(topic)."
        }
        if let d = daysRemaining, (0...21).contains(d), !ahead {
            body += " (\(d)d to the final table)"
        }
        return (title, body)
    }
}

// MARK: - Recalibration plan (mirrors ante/recalibrate.py)

struct SlotPlanRow: Identifiable, Codable {
    var window: StudyWindow
    var minutes: Int
    var role: SlotRole
    var roleDetail: String
    var isPeak: Bool

    var id: String { window.rawValue }
}

struct RecalibrationPlan {
    var available: Bool
    var examDate: Date?
    var daysRemaining: Int?
    var targetScore: Int?
    var recommendedDailyMinutes: Int
    var currentDailyMinutes: Int
    var intensity: String
    var desiredRetention: Double
    var maxIntervalDays: Int?
    var slotPlan: [SlotPlanRow]
    var pacingMessage: String
    var headline: String
    var onTrack: Bool

    /// Recompute the whole plan from the exam date + profile + workload. Mirrors
    /// `recalibrate.recalibrate`, minus the desktop-only diagnostic target-gap
    /// factor (the phone has no baseline diagnostic, so the factor is 1.0).
    static func compute(
        profile: StudyProfile,
        dueCount: Int = 0,
        remainingMinutes: Double = 0,
        topicsRemaining: Int = 0
    ) -> RecalibrationPlan {
        let days = profile.daysRemaining
        let retention = desiredRetention(daysRemaining: days)
        let maxIv: Int? = days.map { max(1, $0) }

        let recommended: Int
        if let d = days {
            let studyDays = max(1.0, ceil(Double(d) * AnConfig.studyDaysPerWeek / 7.0))
            let neededForMastery = remainingMinutes > 0 ? remainingMinutes / studyDays : 0.0
            let reviewOverhead = Double(min(dueCount, 40)) * AnConfig.secondsPerCard / 60.0
            let needed = neededForMastery + reviewOverhead
            var rec = max(
                AnConfig.minDailyMinutes,
                min(AnConfig.maxDailyMinutes, Int(ceil(needed / 5.0)) * 5)
            )
            if d <= 14 { rec = max(rec, 30) }
            recommended = rec
        } else {
            recommended = profile.dailyMinutes
        }

        let intensity = intensityLabel(dailyMinutes: recommended, daysRemaining: days)
        let slots = buildSlotPlan(profile: profile, dailyMinutes: recommended)

        let deficit = max(0, recommended - profile.dailyMinutes)
        let onTrack = deficit == 0
        let headline: String
        let pacing: String
        if let d = days {
            if d <= 0 {
                pacing = "The final table is here — trust the chips you've banked."
            } else if onTrack {
                pacing = "On pace: ~\(recommended) min/day clears the runway in \(d) days (you budgeted \(profile.dailyMinutes))."
            } else {
                pacing = "\(d) days out you need ~\(recommended) min/day — that's \(deficit) more than your \(profile.dailyMinutes)-min budget. Raise the budget or narrow scope."
            }
            let countdown = d == 0 ? "the final table" : "\(d) day\(d != 1 ? "s" : "") out"
            headline = "\(countdown) · ~\(recommended) min/day · target retention \(Int(retention * 100))%"
        } else {
            pacing = "Set your exam date and the whole plan snaps to it."
            headline = "No exam date yet — add one and the Ledger recalibrates everything."
        }

        return RecalibrationPlan(
            available: days != nil,
            examDate: profile.examDate,
            daysRemaining: days,
            targetScore: profile.targetScore,
            recommendedDailyMinutes: recommended,
            currentDailyMinutes: profile.dailyMinutes,
            intensity: intensity,
            desiredRetention: retention,
            maxIntervalDays: maxIv,
            slotPlan: slots,
            pacingMessage: pacing,
            headline: headline,
            onTrack: onTrack
        )
    }

    /// Ramp FSRS desired retention from floor (far out) to ceiling (near test).
    static func desiredRetention(daysRemaining: Int?) -> Double {
        guard let d = daysRemaining else { return round2(AnConfig.retentionFloor) }
        if d <= 0 { return round2(AnConfig.retentionCeiling) }
        if d >= AnConfig.retentionRampDays { return round2(AnConfig.retentionFloor) }
        let frac = Double(AnConfig.retentionRampDays - d) / Double(AnConfig.retentionRampDays)
        let r = AnConfig.retentionFloor + (AnConfig.retentionCeiling - AnConfig.retentionFloor) * frac
        return round2(r)
    }

    static func intensityLabel(dailyMinutes: Int, daysRemaining: Int?) -> String {
        if let d = daysRemaining, d <= 7 { return "crunch" }
        if dailyMinutes <= 30 { return "relaxed" }
        if dailyMinutes <= 90 { return "steady" }
        if dailyMinutes <= 165 { return "intensive" }
        return "crunch"
    }

    /// Assign each window a role, ordered by chronotype (mirrors _slot_roles).
    static func slotRoles(
        windows: [StudyWindow],
        chronotype: Chronotype
    ) -> [StudyWindow: (SlotRole, String)] {
        let peak: StudyWindow
        switch chronotype {
        case .lark: peak = .morning
        case .owl: peak = .night
        case .neutral: peak = .duringTheDay
        }
        var roles: [StudyWindow: (SlotRole, String)] = [:]
        for w in windows {
            if w == .night {
                roles[w] = (.encode, "one light midnight hand — sleep banks it")
            } else if w == peak {
                roles[w] = (.new, "new + hardest tables — your sharpest window")
            } else {
                roles[w] = (.review, "spaced recall of ripe cards")
            }
        }
        if peak == .night, roles[.night] != nil {
            roles[.night] = (.new, "new + hard tables, then a light midnight hand")
        }
        return roles
    }

    /// Split the daily budget across windows, weighting the peak heavier and the
    /// pre-sleep window lighter (mirrors _slot_plan).
    static func buildSlotPlan(profile: StudyProfile, dailyMinutes: Int) -> [SlotPlanRow] {
        let windows = profile.studyWindows.isEmpty
            ? [StudyWindow.morning, .duringTheDay, .night]
            : profile.studyWindows
        let roles = slotRoles(windows: windows, chronotype: profile.chronotype)

        var weights: [StudyWindow: Double] = [:]
        for w in windows {
            let kind = roles[w]?.0 ?? .review
            weights[w] = kind == .new ? 1.3 : (w == .night ? 0.7 : 1.0)
        }
        let totalW = max(weights.values.reduce(0, +), 1.0)

        var plan: [SlotPlanRow] = []
        var assigned = 0
        for (i, w) in windows.enumerated() {
            let mins: Int
            if i == windows.count - 1 {
                mins = max(0, dailyMinutes - assigned)
            } else {
                mins = Int((Double(dailyMinutes) * (weights[w] ?? 1.0) / totalW).rounded())
                assigned += mins
            }
            let (kind, detail) = roles[w] ?? (.review, "spaced retrieval of due cards")
            plan.append(
                SlotPlanRow(
                    window: w,
                    minutes: mins,
                    role: kind,
                    roleDetail: detail,
                    isPeak: kind == .new
                )
            )
        }
        return plan
    }

    private static func round2(_ x: Double) -> Double {
        (x * 100).rounded() / 100
    }
}
