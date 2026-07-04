// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
//
// The Ledger, recalibrated to the exam date. It shows the derived numbers (days
// to the final table, minutes/day, the target-retention ramp, the interval
// cap), the shape of the day (which window plays new vs. review vs. the
// midnight hand), a preview of the calls that shape produces, and an inline
// editor for the two levers that move everything: the exam date and the
// target score.

import SwiftUI

struct PlanView: View {
    @EnvironmentObject private var model: AppModel

    private let statColumns = [
        GridItem(.flexible(), spacing: AnSpace.md),
        GridItem(.flexible(), spacing: AnSpace.md),
    ]

    // Inline editor state, seeded once from the profile.
    @State private var examDate = Date()
    @State private var targetScore = 512
    @State private var remindersEnabled = true
    @State private var rewardsOptIn = false
    @State private var quietStart = AnConfig.quietStartHour
    @State private var quietEnd = AnConfig.quietEndHour
    @State private var seeded = false
    @State private var pending: [PendingReminder] = []

    var body: some View {
        let plan = model.plan
        return AnScreen(issue: "The Ledger · set to your exam") {
            if plan.available {
                stats(plan)
                Text(plan.pacingMessage)
                    .font(.system(size: 14, design: .serif))
                    .foregroundStyle(Color.anMuted)
                    .lineSpacing(3)
                SectionHeader(number: "♦", title: "Shape of the day", meta: plan.intensity)
                ForEach(plan.slotPlan) { slot in
                    slotRow(slot)
                }
                SectionHeader(number: "♦", title: "The day's calls", meta: remindersEnabled ? "on" : "off")
                reminderPreview
            } else {
                Text("Set your exam date and the Ledger recomputes everything — nightly minutes, review intervals, and when the games open.")
                    .font(.system(size: 15, design: .serif))
                    .foregroundStyle(Color.anMuted)
                    .lineSpacing(3)
            }
            SectionHeader(number: "♦", title: "Exam & levers", meta: "recalibrate")
            editor
        }
        .task {
            seed()
            pending = await model.notifications.previewPending()
        }
    }

    // MARK: Stats

    private func stats(_ plan: RecalibrationPlan) -> some View {
        VStack(alignment: .leading, spacing: AnSpace.md) {
            LazyVGrid(columns: statColumns, alignment: .leading, spacing: AnSpace.md) {
                StatBlock(value: "\(plan.daysRemaining ?? 0)", caption: "days to the final table")
                StatBlock(
                    value: "\(plan.recommendedDailyMinutes)",
                    caption: "min/day · \(plan.intensity)",
                    tint: plan.onTrack ? .anInk : .anSignal
                )
                StatBlock(value: "\(Int(plan.desiredRetention * 100))%", caption: "target retention")
                StatBlock(value: "\(plan.maxIntervalDays ?? 0)", caption: "max interval (d)")
            }
            Text("Target retention ramps \(Int(AnConfig.retentionFloor * 100))% → \(Int(AnConfig.retentionCeiling * 100))% inside \(AnConfig.retentionRampDays) days; intervals are capped at the exam so no card is dealt after the final table.")
                .anNote(color: .anFaint, size: 11)
        }
        .anPanel(fill: .anPanel2, stroke: .anRuleStrong, padding: 18)
    }

    // MARK: Slot row

    private func slotRow(_ slot: SlotPlanRow) -> some View {
        HStack(alignment: .center, spacing: AnSpace.md) {
            VStack(alignment: .leading, spacing: 2) {
                Text("\(slot.minutes)")
                    .font(.system(size: 26, weight: .heavy, design: .serif))
                    .monospacedDigit()
                    .foregroundStyle(Color.anInk)
                Text("min").anMicroLabel(color: .anFaint, size: 9)
            }
            .frame(width: 58, alignment: .leading)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    if slot.isPeak {
                        Image(systemName: "suit.diamond.fill")
                            .font(.system(size: 8))
                            .foregroundStyle(Color.anBrass)
                    }
                    Text(slot.window.label).anMicroLabel(color: .anMuted, size: 10.5)
                }
                Text(slot.roleDetail)
                    .font(.system(size: 14, design: .serif))
                    .foregroundStyle(Color.anInk)
            }
            Spacer(minLength: 0)
        }
        .anPanel(fill: .anPanel2, stroke: .anRule, padding: 14)
    }

    // MARK: Reminder preview

    @ViewBuilder
    private var reminderPreview: some View {
        let reminders = model.reminders
        if reminders.isEmpty {
            Text(remindersEnabled ? "No calls scheduled right now — you're clear." : "Calls are off.")
                .anNote(color: .anMuted)
                .anPanel(fill: .anPanel2, stroke: .anRule)
        } else {
            VStack(spacing: AnSpace.sm) {
                ForEach(reminders) { reminder in
                    HStack(alignment: .top, spacing: AnSpace.md) {
                        Text(reminder.clock)
                            .font(.system(size: 15, weight: .bold, design: .monospaced))
                            .foregroundStyle(Color.anBrass)
                            .frame(width: 52, alignment: .leading)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(reminder.title)
                                .font(.system(size: 15, weight: .semibold, design: .serif))
                                .foregroundStyle(Color.anInk)
                            Text(reminder.body).anNote(color: .anMuted, size: 11)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .anPanel(fill: .anPanel2, stroke: .anRule, padding: 14)
                }
                if !pending.isEmpty {
                    Text("\(pending.count) armed on this device · fires with the app closed")
                        .anMicroLabel(color: .anFaint, size: 9.5)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
    }

    // MARK: Editor

    private var editor: some View {
        VStack(alignment: .leading, spacing: AnSpace.lg) {
            VStack(alignment: .leading, spacing: AnSpace.sm) {
                Text("Exam date").anMicroLabel(size: 10.5)
                DatePicker("", selection: $examDate, in: Date()..., displayedComponents: .date)
                    .datePickerStyle(.compact)
                    .labelsHidden()
                    .tint(.anBrass)
            }
            VStack(alignment: .leading, spacing: AnSpace.sm) {
                Text("Target score · \(targetScore)").anMicroLabel(size: 10.5)
                Slider(value: targetBinding, in: 472...528, step: 1).tint(.anBrass)
            }
            AnToggleRow(
                title: "Game calls",
                caption: "Cue-anchored calls when your games open. Suppressed in quiet hours. No shame, ever.",
                isOn: $remindersEnabled
            )
            if remindersEnabled {
                VStack(alignment: .leading, spacing: AnSpace.sm) {
                    Stepper(value: $quietStart, in: 0...23) {
                        Text("Quiet hours start · \(String(format: "%02d:00", quietStart))").anNote(size: 12)
                    }
                    Stepper(value: $quietEnd, in: 0...23) {
                        Text("Quiet hours end · \(String(format: "%02d:00", quietEnd))").anNote(size: 12)
                    }
                }
            }
            AnToggleRow(
                title: "The run & gift-card rewards",
                caption: "\(AnConfig.runTargetNights) straight nights of real play and the House buys dinner. Effort-gated, capped. Off by default.",
                isOn: $rewardsOptIn
            )
            Button {
                recalibrate()
            } label: {
                Text("Recalibrate")
                    .font(.system(size: 13, weight: .semibold, design: .monospaced))
                    .tracking(0.8)
                    .textCase(.uppercase)
                    .foregroundStyle(Color.anCardInk)
                    .padding(.vertical, 13)
                    .frame(maxWidth: .infinity)
                    .background(Color.anBrass)
            }
            .buttonStyle(.plain)
        }
        .anPanel(fill: .anPanel, stroke: .anRuleStrong, padding: 18)
    }

    private var targetBinding: Binding<Double> {
        Binding(
            get: { Double(targetScore) },
            set: { targetScore = Int($0.rounded()) }
        )
    }

    // MARK: Logic

    private func seed() {
        guard !seeded else { return }
        let profile = model.profile
        examDate = profile.examDate ?? Calendar.current.date(byAdding: .month, value: 3, to: Date()) ?? Date()
        targetScore = profile.targetScore ?? 512
        remindersEnabled = profile.remindersEnabled
        rewardsOptIn = profile.rewardsOptIn
        quietStart = profile.quietStartHour
        quietEnd = profile.quietEndHour
        seeded = true
    }

    private func recalibrate() {
        var profile = model.profile
        profile.examDate = examDate
        profile.targetScore = targetScore
        profile.remindersEnabled = remindersEnabled
        profile.rewardsOptIn = rewardsOptIn
        profile.quietStartHour = quietStart
        profile.quietEndHour = quietEnd
        model.updateProfile(profile)
        Task { @MainActor in
            pending = await model.notifications.previewPending()
        }
    }
}
