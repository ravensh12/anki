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

    // The live engine + den link (nil while the client serves sample data).
    @Published private(set) var engineStatus: EngineStatus?
    /// True after the first engine status read finishes (so sample-data badge
    /// does not flash before we know whether the Rust core started).
    private var engineStatusResolved = false
    /// "Just show me the den" — waves the door away for this launch only.
    @Published private(set) var accountSkipped = false
    @Published private(set) var isSyncing = false
    /// The last sync outcome, verbatim and honest ("Up to date with the den.").
    @Published private(set) var syncLine: String?

    let engine: EngineClient
    let notifications: NotificationScheduler

    private let defaults: UserDefaults
    private let profileKey = "ante.profile.v1"
    private let ritualKey = "ante.ritual.v1"
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()
    private var autoConnectAttempted = false
    private var initialSyncDone = false

    // MARK: Init

    init(
        engine: EngineClient = MockEngine(),
        notifications: NotificationScheduler = NotificationScheduler(),
        defaults: UserDefaults = .standard
    ) {
        self.engine = engine
        self.notifications = notifications
        self.defaults = defaults
        var loaded = Self.load(StudyProfile.self, key: profileKey, from: defaults, decoder: decoder)
            ?? .default
        // The door runs every launch — local sign-in is session-only.
        loaded.account = nil
        self.profile = loaded
        self.ritual = Self.load(RitualState.self, key: ritualKey, from: defaults, decoder: decoder)
            ?? .empty
        // Demo/e2e hook: a launch environment that names a den skips the
        // hand-onboarding so the seat can be driven end-to-end unattended.
        if !profile.onboarded, Self.launchDen() != nil {
            var auto = profile
            auto.examDate = Calendar.current.date(byAdding: .day, value: 90, to: Date())
            auto.onboarded = true
            profile = auto
            persist(auto, key: profileKey)
        }
        // Demo/e2e hook: open on a chosen surface (ANTE_TAB=atlas|session|plan).
        switch ProcessInfo.processInfo.environment["ANTE_TAB"] {
        case "atlas": selectedTab = .atlas
        case "session": selectedTab = .session
        case "plan": selectedTab = .plan
        default: break
        }
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

    /// Show the door after onboarding until this seat has signed in with a
    /// local den account (email + name), mirroring the desktop's account gate.
    /// Env-driven launches (demo/e2e) seat themselves and never see the door.
    var needsAccount: Bool {
        profile.onboarded
            && profile.account == nil
            && !accountSkipped
            && Self.launchDen() == nil
    }

    /// True when the screen is showing `MockEngine` sample data rather than the
    /// live Rust engine — i.e. the engine could not start (`liveStatus()` is
    /// nil) once the first status read has settled. The UI surfaces a visible
    /// "SAMPLE DATA" badge in this state so mocked, abstaining numbers are never
    /// mistaken for a real reading (the honesty rule: no made-up numbers).
    var usingSampleData: Bool {
        engineStatusResolved && engineStatus == nil
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
        engineStatus = await engine.liveStatus()
        engineStatusResolved = true
        await autoConnectIfLaunchedWithDen()
        do {
            self.scores = try await engine.fetchScores()
            self.mastery = try await engine.fetchMastery()
            self.due = try await engine.fetchDue()
        } catch {
            lastError = error.localizedDescription
        }
        isLoading = false
        await rescheduleNotifications()
        // First read of a wired seat brings it current without a tap.
        if !initialSyncDone, engineStatus?.connected == true {
            initialSyncDone = true
            Task { await syncNow() }
        }
    }

    // MARK: The den link (sync)

    /// Join a den: log into the self-hosted sync server and pull its collection.
    func connectDen(endpoint: String, username: String, password: String) async {
        isSyncing = true
        lastError = nil
        do {
            syncLine = try await engine.connect(
                endpoint: endpoint, username: username, password: password)
        } catch {
            lastError = error.localizedDescription
        }
        isSyncing = false
        engineStatus = await engine.liveStatus()
        await refresh()
    }

    func disconnectDen() async {
        await engine.disconnect()
        syncLine = nil
        engineStatus = await engine.liveStatus()
    }

    // MARK: The door (local account — mirrors the desktop's email sign-in)

    /// Sign in with a local den account (email + optional name), then persist.
    /// Identity only, like the desktop's email sign-in; sync stays separate.
    func signInAccount(email: String, name: String) {
        var next = profile
        next.account = AnteAccount(
            email: email.trimmingCharacters(in: .whitespaces),
            name: name.trimmingCharacters(in: .whitespaces)
        )
        applyProfile(next)
    }

    /// Sign out of the local account, returning this seat to the door.
    func signOutAccount() {
        var next = profile
        next.account = nil
        accountSkipped = false
        applyProfile(next)
    }

    /// "Just show me the den" — dismisses the door until the next launch.
    func skipAccount() {
        accountSkipped = true
    }

    /// Two-way sync with the joined den, then re-read everything.
    func syncNow() async {
        guard engineStatus?.connected == true, !isSyncing else { return }
        isSyncing = true
        lastError = nil
        do {
            syncLine = try await engine.sync()
        } catch {
            lastError = error.localizedDescription
        }
        isSyncing = false
        await refresh()
    }

    /// Answer a dealt card through the real scheduler (rating 0–3 = Again…Easy).
    func answerCard(id: String, rating: Int, millisecondsTaken: Int) async {
        do {
            try await engine.answer(
                cardID: id, rating: rating, millisecondsTaken: millisecondsTaken)
        } catch {
            lastError = error.localizedDescription
        }
    }

    /// Launch-environment den for unattended demo/e2e runs
    /// (ANTE_SYNC_ENDPOINT / ANTE_SYNC_USER / ANTE_SYNC_PASS).
    private static func launchDen() -> (endpoint: String, user: String, pass: String)? {
        let env = ProcessInfo.processInfo.environment
        guard let endpoint = env["ANTE_SYNC_ENDPOINT"], !endpoint.isEmpty else { return nil }
        return (endpoint, env["ANTE_SYNC_USER"] ?? "ante", env["ANTE_SYNC_PASS"] ?? "ante123")
    }

    private func autoConnectIfLaunchedWithDen() async {
        guard !autoConnectAttempted,
            let den = Self.launchDen(),
            let status = engineStatus
        else { return }
        autoConnectAttempted = true
        isSyncing = true
        do {
            if status.connected {
                // Already wired: an env-driven launch just brings the seat
                // current, so demo runs are idempotent.
                syncLine = try await engine.sync()
            } else {
                syncLine = try await engine.connect(
                    endpoint: den.endpoint, username: den.user, password: den.pass)
            }
        } catch {
            lastError = error.localizedDescription
        }
        // Visible on the launch console so unattended runs can be verified.
        print("[ante] auto-wire \(den.endpoint): \(syncLine ?? lastError ?? "no outcome")")
        isSyncing = false
        engineStatus = await engine.liveStatus()
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
        var durable = newProfile
        durable.account = nil
        persist(durable, key: profileKey)
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
