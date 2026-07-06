// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
//
// The app entry point. It builds the shared AppModel (backed by SyncedEngine,
// which transparently serves sample data until the Rust xcframework is wired in),
// injects it into the environment, and shows onboarding until the student has
// set their exam date — after which the main tabs take over.

import SwiftUI

@main
struct AnteApp: App {
    @StateObject private var model = AppModel(engine: SyncedEngine())
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .tint(.anBrass)
                .preferredColorScheme(.dark)
        }
        .onChange(of: scenePhase) { _, phase in
            // Picking the phone up is the sync gesture: whatever the desktop
            // banked lands on this seat the moment the app comes forward.
            if phase == .active {
                Task { await model.syncNow() }
            }
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Group {
            if !model.profile.onboarded {
                OnboardingView()
            } else if model.needsAccount {
                DenLoginView()
            } else {
                MainTabView()
            }
        }
        .task { await model.refresh() }
    }
}
