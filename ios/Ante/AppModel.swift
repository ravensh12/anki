// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
//
// The single source of app state. It owns the durable study profile (persisted
// as JSON in UserDefaults), the latest engine reads (scores, mastery, due
// stack), and the daily-ritual state, and it keeps the local-notification
// schedule in step with the profile. Views observe it; nothing else holds
// mutable app state.

import Foundation
import SwiftUI

@MainActor
final class AppModel: ObservableObject {
    // Durable + derived state the UI observes.
    @Published private(set) var profile: StudyProfile
    @Published private(set) var ritual: RitualState
    @Published private(set) var scores: ScoresSnapshot?
    @Published private(set) var mastery: [TopicMastery] = []
    @Published private(set) var due: DueQueue = .empty
    @Published private(set) var isLoading = false
    @Published private(set) var lastError: String?
    /// The selected tab, so a screen's primary CTA can route to another surface.
    @Published var selectedTab: AppTab = .today

    let engine: EngineClient
    let notifications: NotificationScheduler

    private let defaults: UserDefaults
    private let profileKey = "ante.profile.v1"
    private let ritualKey = "ante.ritual.v1"
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    // MARK: Init

    init(
        engine: EngineClient = MockEngine(),
        notifications: NotificationScheduler = NotificationScheduler(),
        defaults: UserDefaults = .standard
    ) {
        self.engine = engine
        self.notifications = notifications
        self.defaults = defaults
        self.profile = Self.load(StudyProfile.self, key: profileKey, from: defaults, decoder: decoder)
            ?? .default
        self.ritual = Self.load(RitualState.self, key: ritualKey, from: defaults, decoder: decoder)
            ?? .empty
        normalizeRitualForToday()
    }

    // MARK: Derived state

    /// The plan recomputed from the current profile + workload.
    var plan: RecalibrationPlan {
        RecalibrationPlan.compute(profile: profile, dueCount: due.dueCardCount)
    }

    /// Today's notification schedule (honest copy, quiet-hours aware).
    var reminders: [Reminder] {
        ReminderBuilder.schedule(
            profile: profile,
            slotPlan: plan.slotPlan,
            dueCount: due.dueCardCount,
            bestNextTopic: due.bestNextTopic,
            daysRemaining: profile.daysRemaining
        )
    }

    var masteryBySection: [MCATSection: [TopicMastery]] {
        Dictionary(grouping: mastery, by: \.section)
    }

    // MARK: Intents

    /// Finish first-run onboarding: mark onboarded, persist, arm notifications.
    func completeOnboarding(_ profile: StudyProfile) {
        var next = profile
        next.onboarded = true
        applyProfile(next)
    }

    /// Persist an edited profile (Plan inline edits, reminder/reward toggles) and
    /// re-arm the notification schedule to match.
    func updateProfile(_ profile: StudyProfile) {
        applyProfile(profile)
    }

    /// Pull fresh scores, mastery, and the due stack from the engine, then
    /// reschedule notifications so their copy reflects the real workload.
    func refresh() async {
        normalizeRitualForToday()
        isLoading = true
        lastError = nil
        do {
            self.scores = try await engine.fetchScores()
            self.mastery = try await engine.fetchMastery()
            self.due = try await engine.fetchDue()
        } catch {
            lastError = error.localizedDescription
        }
        isLoading = false
        await rescheduleNotifications()
    }

    /// Record that a bookend session (morning or night) was completed, advancing
    /// the streak on the first completion of a fresh day.
    func markSessionDone(window: RitualWindow) {
        let cal = Calendar.current
        let today = cal.startOfDay(for: Date())
        let isNewDay = ritual.lastCompletedDay.map { !cal.isDate($0, inSameDayAs: today) } ?? true

        if isNewDay {
            let continues: Bool = {
                guard
                    let last = ritual.lastCompletedDay,
                    let yesterday = cal.date(byAdding: .day, value: -1, to: today)
                else { return false }
                return cal.isDate(last, inSameDayAs: yesterday)
            }()
            ritual.morningDone = false
            ritual.nightDone = false
            ritual.streak = continues ? ritual.streak + 1 : 1
        }

        switch window {
        case .morning: ritual.morningDone = true
        case .night: ritual.nightDone = true
        }
        ritual.lastCompletedDay = today
        persist(ritual, key: ritualKey)
    }

    /// Ask for notification permission and arm the current schedule.
    func enableNotificationsIfNeeded() async {
        await notifications.requestAuthorization()
        await rescheduleNotifications()
    }

    // MARK: - Private

    private func applyProfile(_ newProfile: StudyProfile) {
        profile = newProfile
        persist(newProfile, key: profileKey)
        Task { await rescheduleNotifications() }
    }

    private func rescheduleNotifications() async {
        if profile.remindersEnabled {
            await notifications.reschedule(reminders: reminders)
        } else {
            await notifications.cancelAll()
        }
    }

    private func normalizeRitualForToday() {
        let cal = Calendar.current
        let today = cal.startOfDay(for: Date())
        if let last = ritual.lastCompletedDay, !cal.isDate(last, inSameDayAs: today) {
            ritual.morningDone = false
            ritual.nightDone = false
        }
    }

    private func persist<T: Encodable>(_ value: T, key: String) {
        if let data = try? encoder.encode(value) {
            defaults.set(data, forKey: key)
        }
    }

    private static func load<T: Decodable>(
        _ type: T.Type,
        key: String,
        from defaults: UserDefaults,
        decoder: JSONDecoder
    ) -> T? {
        guard let data = defaults.data(forKey: key) else { return nil }
        return try? decoder.decode(T.self, from: data)
    }
}
