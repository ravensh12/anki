// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
//
// The nightly face of the den, deliberately spare: a countdown to the final
// table, one primary action (take your seat), the Book's honest line (a number
// with a range, or a stamped abstention when the evidence is thin), the two
// games of the day — the Morning Game and the Midnight Game — and the run.

import SwiftUI

struct TodayView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        AnScreen(issue: issueLine) {
            countdown
            wireStrip
            primaryCTA
            SectionHeader(number: "♦", title: "The Book's line", meta: "scale 472–528")
            verdict
            SectionHeader(number: "♠", title: "The two games", meta: "morning + midnight")
            bookends
        }
        .task { await model.refresh() }
    }

    // MARK: The wire (live engine + den sync, at a glance)

    @ViewBuilder
    private var wireStrip: some View {
        if let status = model.engineStatus {
            HStack(spacing: AnSpace.sm) {
                Circle()
                    .fill(status.connected ? Color.anGood : Color.anMuted)
                    .frame(width: 7, height: 7)
                if status.connected {
                    Text(model.isSyncing ? "syncing…" : (model.syncLine ?? "wired to the den"))
                        .anMicroLabel(color: .anMuted, size: 9.5)
                        .lineLimit(1)
                    Spacer(minLength: 0)
                    Button {
                        Task { await model.syncNow() }
                    } label: {
                        Text("Sync").anMicroLabel(color: .anBrass, size: 10)
                    }
                    .buttonStyle(.plain)
                    .disabled(model.isSyncing)
                } else {
                    Text("shared engine live · not wired to a den")
                        .anMicroLabel(color: .anMuted, size: 9.5)
                    Spacer(minLength: 0)
                    Button {
                        model.selectedTab = .plan
                    } label: {
                        Text("Wire it").anMicroLabel(color: .anBrass, size: 10)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(Color.anPanel)
            .overlay(
                RoundedRectangle(cornerRadius: AnSpace.radius)
                    .strokeBorder(Color.anRule, lineWidth: 1)
            )
        }
    }

    private var issueLine: String {
        "Tonight · " + Date().formatted(date: .abbreviated, time: .omitted)
    }

    // MARK: Countdown

    private var countdown: some View {
        let plan = model.plan
        return HStack(alignment: .center, spacing: AnSpace.lg) {
            VStack(alignment: .leading, spacing: AnSpace.xs) {
                Text(countdownLabel(plan.daysRemaining))
                    .font(.system(size: 46, weight: .heavy, design: .serif))
                    .monospacedDigit()
                    .foregroundStyle(Color.anBrass)
                Text(examLine(plan)).anMicroLabel(color: .anMuted, size: 10.5)
            }
            Spacer()
            if plan.available {
                VStack(alignment: .trailing, spacing: AnSpace.xs) {
                    Text("\(plan.recommendedDailyMinutes) min/night")
                        .font(.system(size: 18, weight: .bold, design: .serif))
                        .foregroundStyle(Color.anInk)
                    Text("target retention \(Int(plan.desiredRetention * 100))%")
                        .anMicroLabel(color: .anFaint, size: 10)
                }
            }
        }
        .anPanel(fill: .anPanel2, stroke: .anRule, padding: 18)
    }

    private func countdownLabel(_ days: Int?) -> String {
        guard let days else { return "D–?" }
        if days > 0 { return "D–\(days)" }
        if days == 0 { return "FINAL TABLE" }
        return "D+\(-days)"
    }

    private func examLine(_ plan: RecalibrationPlan) -> String {
        guard let date = plan.examDate else { return "No exam date yet — set it in the Ledger" }
        return "to the final table · " + date.formatted(date: .abbreviated, time: .omitted)
    }

    // MARK: Primary CTA (the one action)

    @ViewBuilder
    private var primaryCTA: some View {
        let cardsDue = model.due.dueCardCount
        let itemsDue = model.due.dueItemCount
        if cardsDue > 0 || itemsDue > 0 {
            AnCTAButton(
                lead: "Take your seat",
                meta: "\(cardsDue) cards + \(itemsDue) questions · stakes ordered",
                symbol: "suit.spade.fill",
                tone: .signal
            ) {
                model.selectedTab = .session
            }
        } else {
            AnCTAButton(
                lead: "The felt is clear",
                meta: "nothing on the table · rest is part of the plan",
                symbol: "checkmark",
                tone: .good
            ) {
                model.selectedTab = .session
            }
        }
    }

    // MARK: Verdict

    @ViewBuilder
    private var verdict: some View {
        if let readiness = model.scores?.readiness {
            if readiness.abstained || readiness.value == nil {
                AbstainVerdict(reasons: readiness.reasons)
            } else {
                ReadingVerdict(reading: readiness)
            }
        } else {
            Text(model.isLoading ? "Reading the Book…" : "No line yet.")
                .anNote(color: .anMuted)
                .anPanel(fill: .anPanel2, stroke: .anRule)
        }
    }

    // MARK: The two games

    private var bookends: some View {
        VStack(spacing: AnSpace.md) {
            BookendCard(
                kicker: "Morning Game",
                headline: "Sit down cold — before coffee.",
                detail: "Cold recall beats warm rereading. The deck is already stacked in your favor.",
                hour: StudyWindow.morning.hour,
                done: model.ritual.morningDone
            ) {
                model.markSessionDone(window: .morning)
            }
            BookendCard(
                kicker: "Midnight Game",
                headline: "One light hand before lights out.",
                detail: "Play it now and your brain banks it overnight — the cheapest minutes of the day.",
                hour: StudyWindow.night.hour,
                done: model.ritual.nightDone
            ) {
                model.markSessionDone(window: .night)
            }
            runStrip
        }
    }

    /// The 30-night run tracker: nights kept, the gift-card target, honest copy.
    @ViewBuilder
    private var runStrip: some View {
        let streak = model.ritual.streak
        if streak > 0 {
            VStack(alignment: .leading, spacing: AnSpace.sm) {
                HStack(alignment: .firstTextBaseline) {
                    Text("Night \(min(streak, AnConfig.runTargetNights)) of \(AnConfig.runTargetNights)")
                        .font(.system(size: 17, weight: .bold, design: .serif))
                        .foregroundStyle(Color.anBrass)
                    Spacer()
                    Text("the run").anMicroLabel(color: .anFaint, size: 10)
                }
                AnMeter(
                    fraction: Double(streak) / Double(AnConfig.runTargetNights),
                    fill: .anBrass,
                    height: 6
                )
                Text(
                    streak >= AnConfig.runTargetNights
                        ? "Thirty straight nights — the House buys dinner. The gift card is yours."
                        : "\(AnConfig.runTargetNights - streak) nights to the gift card · kept by real play, not logins"
                )
                .anNote(color: .anMuted, size: 11)
            }
            .anPanel(fill: .anPanel, stroke: .anRule, padding: 14)
        }
    }
}

// MARK: - Verdict panels

private struct ReadingVerdict: View {
    var reading: ScoreReading

    var body: some View {
        let value = reading.value ?? 0
        VStack(alignment: .leading, spacing: AnSpace.md) {
            HStack(alignment: .firstTextBaseline, spacing: AnSpace.sm) {
                Text("\(Int(value.rounded()))")
                    .font(.system(size: 62, weight: .heavy, design: .serif))
                    .monospacedDigit()
                    .foregroundStyle(Color.anInk)
                Text(rangeCaption)
                    .anMicroLabel(color: .anMuted, size: 11)
            }
            AnMeter(
                fraction: scaleFraction(value),
                bandLower: reading.lower.map(scaleFraction),
                bandUpper: reading.upper.map(scaleFraction),
                fill: .anBrass,
                height: 10
            )
            HStack {
                Text("\(AnConfig.scaleMin)").anMicroLabel(color: .anFaint, size: 9)
                Spacer()
                Text("\(AnConfig.scaleMax)").anMicroLabel(color: .anFaint, size: 9)
            }
        }
        .anPanel(fill: .anPanel2, stroke: .anRule, padding: 18)
    }

    private var rangeCaption: String {
        var parts = ["the line"]
        if let lower = reading.lower, let upper = reading.upper {
            parts.append("\(Int(lower.rounded()))–\(Int(upper.rounded()))")
        }
        if let confidence = reading.confidence {
            parts.append("\(confidence) conf.")
        }
        return parts.joined(separator: " · ")
    }

    private func scaleFraction(_ score: Double) -> Double {
        let span = Double(AnConfig.scaleMax - AnConfig.scaleMin)
        return max(0, min(1, (score - Double(AnConfig.scaleMin)) / span))
    }
}

private struct AbstainVerdict: View {
    var reasons: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: AnSpace.md) {
            Text("THE BOOK ABSTAINS")
                .font(.system(size: 13, weight: .bold, design: .monospaced))
                .tracking(1.6)
                .foregroundStyle(Color.anSignal)
                .padding(.vertical, 7)
                .padding(.horizontal, 12)
                .overlay(
                    RoundedRectangle(cornerRadius: AnSpace.radius)
                        .strokeBorder(Color.anSignal, lineWidth: 2)
                )
                .rotationEffect(.degrees(-3))
                .padding(.top, AnSpace.xs)
            Text("The Book doesn't post odds it can't back. A confident number with nothing behind it is a guess in a nice font — win application hands to earn a line.")
                .font(.system(size: 15, design: .serif))
                .foregroundStyle(Color.anInk)
                .lineSpacing(3)
            if !reasons.isEmpty {
                VStack(alignment: .leading, spacing: AnSpace.sm) {
                    ForEach(reasons, id: \.self) { reason in
                        HStack(alignment: .top, spacing: 8) {
                            Text("—").foregroundStyle(Color.anFaint)
                            Text(reason).anNote(color: .anMuted, size: 11.5)
                        }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .anPanel(fill: .anPanel2, stroke: .anRule, padding: 18)
    }
}

// MARK: - Game card (the daily bookends)

private struct BookendCard: View {
    var kicker: String
    var headline: String
    var detail: String
    var hour: Int
    var done: Bool
    var action: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: AnSpace.sm) {
            HStack {
                Text(kicker).anMicroLabel(color: .anBrass, size: 10.5)
                Spacer()
                Text(String(format: "%02d:00", hour)).anMicroLabel(color: .anFaint, size: 10.5)
            }
            Text(headline)
                .font(.system(size: 19, weight: .bold, design: .serif))
                .foregroundStyle(Color.anInk)
            Text(detail).anNote(color: .anMuted, size: 11.5)
            HStack {
                if done {
                    Label("Banked", systemImage: "checkmark.circle.fill")
                        .font(.system(size: 12, weight: .semibold, design: .monospaced))
                        .foregroundStyle(Color.anGood)
                } else {
                    Text("Open").anMicroLabel(color: .anFaint, size: 10.5)
                }
                Spacer()
                Button(action: action) {
                    Text(done ? "Play again" : "Mark played")
                        .font(.system(size: 11, weight: .semibold, design: .monospaced))
                        .tracking(0.6)
                        .textCase(.uppercase)
                        .foregroundStyle(done ? Color.anMuted : Color.anCardInk)
                        .padding(.vertical, 9)
                        .padding(.horizontal, 16)
                        .background(done ? Color.clear : Color.anBrass)
                        .overlay(
                            RoundedRectangle(cornerRadius: AnSpace.radius)
                                .strokeBorder(done ? Color.anRule : .clear, lineWidth: 1)
                        )
                }
                .buttonStyle(.plain)
            }
            .padding(.top, AnSpace.xs)
        }
        .anPanel(
            fill: done ? .anPanel : .anPanel2,
            stroke: done ? .anRule : .anRuleStrong.opacity(0.35),
            padding: 16
        )
    }
}
