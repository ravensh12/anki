// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
//
// First run. The flow is date-first on purpose: the exam date is the lever the
// whole schedule is computed backward from. Three questions — when you sit the
// exam, how your day is shaped, how the House should call you — then a short
// "Recalibrating…" beat that names what's being computed before the app opens.

import SwiftUI

struct OnboardingView: View {
    @EnvironmentObject private var model: AppModel

    @State private var step = 0
    @State private var examDate = Calendar.current.date(byAdding: .month, value: 3, to: Date()) ?? Date()
    @State private var targetScore = 512
    @State private var chronotype: Chronotype = .neutral
    @State private var dailyMinutes = AnConfig.defaultDailyMinutes
    @State private var remindersEnabled = true
    @State private var rewardsOptIn = false

    private var draftProfile: StudyProfile {
        var profile = StudyProfile.default
        profile.examDate = examDate
        profile.targetScore = targetScore
        profile.chronotype = chronotype
        profile.dailyMinutes = dailyMinutes
        profile.remindersEnabled = remindersEnabled
        profile.rewardsOptIn = rewardsOptIn
        return profile
    }

    var body: some View {
        ZStack {
            Color.anPaper.ignoresSafeArea()
            if step == 3 {
                RecalibratingView(examDate: examDate, onDone: finish)
                    .transition(.opacity)
            } else {
                questionFlow
            }
        }
    }

    // MARK: Question flow

    private var questionFlow: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: AnSpace.lg) {
                    Wordmark(size: 28)
                    VStack(alignment: .leading, spacing: AnSpace.sm) {
                        Text("Step \(step + 1) of 3 · \(stepKicker)")
                            .anMicroLabel(color: .anFaint, size: 11)
                        Text(stepQuestion)
                            .anHeading(size: 30)
                        Text(stepSub)
                            .font(.system(size: 15, design: .serif))
                            .foregroundStyle(Color.anMuted)
                            .lineSpacing(3)
                    }
                    stepBody
                }
                .padding(AnSpace.xl)
            }
            actionBar
                .padding(.horizontal, AnSpace.xl)
                .padding(.vertical, AnSpace.lg)
        }
    }

    @ViewBuilder
    private var stepBody: some View {
        switch step {
        case 0: examStep
        case 1: rhythmStep
        default: nudgeStep
        }
    }

    private var examStep: some View {
        VStack(alignment: .leading, spacing: AnSpace.lg) {
            VStack(alignment: .leading, spacing: AnSpace.sm) {
                Text("Exam date").anMicroLabel(size: 10.5)
                DatePicker(
                    "",
                    selection: $examDate,
                    in: Date()...,
                    displayedComponents: .date
                )
                .datePickerStyle(.graphical)
                .labelsHidden()
                .tint(.anBrass)
            }
            VStack(alignment: .leading, spacing: AnSpace.sm) {
                Text("Target score (472–528)").anMicroLabel(size: 10.5)
                HStack(alignment: .firstTextBaseline, spacing: AnSpace.sm) {
                    Text("\(targetScore)")
                        .font(.system(size: 44, weight: .heavy, design: .serif))
                        .monospacedDigit()
                        .foregroundStyle(Color.anInk)
                    Text("/ 528").anMicroLabel(color: .anFaint, size: 12)
                }
                Slider(value: targetBinding, in: 472...528, step: 1).tint(.anBrass)
            }
            .anPanel(fill: .anPanel, stroke: .anRule, padding: 16)
        }
    }

    private var rhythmStep: some View {
        VStack(alignment: .leading, spacing: AnSpace.lg) {
            VStack(alignment: .leading, spacing: AnSpace.sm) {
                Text("Chronotype").anMicroLabel(size: 10.5)
                AnSegmented(
                    options: Chronotype.allCases.map { AnSegment(value: $0, label: $0.label) },
                    selection: $chronotype
                )
            }
            VStack(alignment: .leading, spacing: AnSpace.sm) {
                Text("Daily study budget: \(dailyMinutes) min").anMicroLabel(size: 10.5)
                Slider(value: minutesBinding, in: 15...240, step: 5).tint(.anBrass)
                Text("A ceiling, not a quota. The Ledger recomputes the real number from your exam date.")
                    .anNote(color: .anMuted, size: 11)
            }
            .anPanel(fill: .anPanel, stroke: .anRule, padding: 16)
        }
    }

    private var nudgeStep: some View {
        VStack(alignment: .leading, spacing: AnSpace.md) {
            AnToggleRow(
                title: "Game calls",
                caption: "Cue-anchored calls when your games open, including the midnight hand. Suppressed in quiet hours. No shame, ever.",
                isOn: $remindersEnabled
            )
            .anPanel(fill: .anPanel, stroke: .anRule, padding: 16)

            AnToggleRow(
                title: "The run & gift-card rewards",
                caption: "30 straight nights of real play and the House buys dinner. Effort-gated, capped, never for logins. Off by default.",
                isOn: $rewardsOptIn
            )
            .anPanel(fill: .anPanel, stroke: .anRule, padding: 16)

            Text("Won tables count, not logins.")
                .font(.system(size: 15, design: .serif))
                .italic()
                .foregroundStyle(Color.anMuted)
                .padding(.top, AnSpace.xs)
        }
    }

    private var actionBar: some View {
        HStack(spacing: AnSpace.md) {
            if step > 0 {
                Button {
                    withAnimation { step -= 1 }
                } label: {
                    Text("Back").anMicroLabel(color: .anMuted, size: 12)
                }
                .buttonStyle(.plain)
            }
            HStack(spacing: 7) {
                ForEach(0..<3) { index in
                    Circle()
                        .fill(index == step ? Color.anBrass : Color.anRule)
                        .frame(width: 8, height: 8)
                }
            }
            Spacer()
            Button {
                withAnimation { step = min(step + 1, 3) }
            } label: {
                Text(step < 2 ? "Continue" : "Recalibrate")
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .tracking(0.8)
                    .textCase(.uppercase)
                    .foregroundStyle(Color.anPaper)
                    .padding(.vertical, 12)
                    .padding(.horizontal, 22)
                    .background(Color.anInk)
            }
            .buttonStyle(.plain)
        }
    }

    // MARK: Bindings

    private var targetBinding: Binding<Double> {
        Binding(
            get: { Double(targetScore) },
            set: { targetScore = Int($0.rounded()) }
        )
    }

    private var minutesBinding: Binding<Double> {
        Binding(
            get: { Double(dailyMinutes) },
            set: { dailyMinutes = Int(($0 / 5).rounded()) * 5 }
        )
    }

    // MARK: Copy

    private var stepKicker: String {
        switch step {
        case 0: return "the one date that sets everything"
        case 1: return "the shape of your day"
        default: return "on your terms"
        }
    }

    private var stepQuestion: String {
        switch step {
        case 0: return "When do you sit the MCAT?"
        case 1: return "When is your head clearest?"
        default: return "How should the House call you?"
        }
    }

    private var stepSub: String {
        switch step {
        case 0:
            return "This is the lever. Your whole schedule is computed backward from this day. When you study beats how much."
        case 1:
            return "New and hard tables go in your peak window; the light midnight hand lands right before sleep, where your brain banks it overnight."
        default:
            return "Calls are cue-anchored and suppressed in quiet hours. Rewards are bounded and opt-in — a primer, not a wage."
        }
    }

    // MARK: Completion

    private func finish() {
        Task { @MainActor in
            if draftProfile.remindersEnabled {
                await model.notifications.requestAuthorization()
            }
            model.completeOnboarding(draftProfile)
            await model.refresh()
        }
    }
}

// MARK: - Recalibrating interstitial

private struct RecalibratingView: View {
    var examDate: Date
    var onDone: () -> Void

    @State private var revealed = 0

    private let lines = [
        "Reading your exam date…",
        "Spreading the work across the days left…",
        "Tightening review intervals as test day nears…",
        "Listing the tables, city by city…",
        "Placing hard tables in your sharp window…",
        "Done. The Emerald Room is open.",
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: AnSpace.lg) {
            Wordmark(size: 28)
            Text("Recalibrating everything to \(examDate.formatted(date: .abbreviated, time: .omitted))…")
                .anHeading(size: 24)
            VStack(alignment: .leading, spacing: AnSpace.md) {
                ForEach(Array(lines.enumerated()), id: \.offset) { index, line in
                    if index < revealed {
                        HStack(alignment: .top, spacing: 10) {
                            Text("→").foregroundStyle(Color.anBrass)
                            Text(line).anNote(color: .anMuted, size: 13)
                        }
                        .transition(.opacity)
                    }
                }
            }
            Spacer()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(AnSpace.xl)
        .task { await run() }
    }

    private func run() async {
        for index in 1...lines.count {
            withAnimation(.easeIn(duration: 0.3)) { revealed = index }
            try? await Task.sleep(for: .milliseconds(450))
        }
        try? await Task.sleep(for: .milliseconds(500))
        onDone()
    }
}
