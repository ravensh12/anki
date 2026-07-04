// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
//
// This is why iOS is the right companion for Ante. The morning/midday/night
// bookends are scheduled as repeating local notifications through
// UNUserNotificationCenter, so they fire reliably at the student's windows even
// with the app closed — no background execution, no server round-trip. The copy
// is the same no-shame, cue-anchored text the desktop uses (built in
// `ReminderBuilder`, which already drops any window inside quiet hours). This
// type only turns those `Reminder`s into calendar triggers and keeps the pending
// set in sync with the profile.

import Foundation
import UserNotifications

/// A read-back of a scheduled notification, for the Plan preview.
struct PendingReminder: Identifiable {
    var id: String
    var hour: Int
    var minute: Int
    var title: String
    var body: String

    var clock: String { String(format: "%02d:%02d", hour, minute) }
}

final class NotificationScheduler {
    private let center: UNUserNotificationCenter
    private let identifierPrefix = "ante.reminder."
    private let categoryIdentifier = "ante.session"

    init(center: UNUserNotificationCenter = .current()) {
        self.center = center
    }

    // MARK: Authorization

    /// Ask for permission to post alerts. Returns whether it was granted. iOS
    /// requires no Info.plist key for this — the prompt is driven by this call.
    @discardableResult
    func requestAuthorization() async -> Bool {
        do {
            return try await center.requestAuthorization(options: [.alert, .sound, .badge])
        } catch {
            return false
        }
    }

    func authorizationStatus() async -> UNAuthorizationStatus {
        await center.notificationSettings().authorizationStatus
    }

    // MARK: Scheduling

    /// Replace the current Ante schedule with `reminders`. Each becomes a
    /// daily-repeating `UNCalendarNotificationTrigger` at its window's hour. Only
    /// our own requests are touched, so any unrelated notifications survive.
    func reschedule(reminders: [Reminder]) async {
        await cancelAll()
        for reminder in reminders {
            let content = UNMutableNotificationContent()
            content.title = reminder.title
            content.body = reminder.body
            content.sound = .default
            content.categoryIdentifier = categoryIdentifier
            content.threadIdentifier = reminder.window.rawValue

            var components = DateComponents()
            components.hour = reminder.hour
            components.minute = reminder.minute
            let trigger = UNCalendarNotificationTrigger(dateMatching: components, repeats: true)

            let request = UNNotificationRequest(
                identifier: identifierPrefix + reminder.id,
                content: content,
                trigger: trigger
            )
            try? await center.add(request)
        }
    }

    /// Remove every pending Ante reminder (used when reminders are turned off
    /// and before every reschedule).
    func cancelAll() async {
        let pending = await center.pendingNotificationRequests()
        let ids = pending
            .map(\.identifier)
            .filter { $0.hasPrefix(identifierPrefix) }
        center.removePendingNotificationRequests(withIdentifiers: ids)
    }

    /// The currently scheduled Ante reminders, read back from the system, in
    /// clock order. Lets the Plan screen show exactly what will fire.
    func previewPending() async -> [PendingReminder] {
        let pending = await center.pendingNotificationRequests()
        return pending
            .filter { $0.identifier.hasPrefix(identifierPrefix) }
            .compactMap { request -> PendingReminder? in
                guard let trigger = request.trigger as? UNCalendarNotificationTrigger else {
                    return nil
                }
                let hour = trigger.dateComponents.hour ?? 0
                let minute = trigger.dateComponents.minute ?? 0
                return PendingReminder(
                    id: request.identifier,
                    hour: hour,
                    minute: minute,
                    title: request.content.title,
                    body: request.content.body
                )
            }
            .sorted { ($0.hour, $0.minute) < ($1.hour, $1.minute) }
    }
}
