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

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .tint(.anBrass)
                .preferredColorScheme(.dark)
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Group {
            if model.profile.onboarded {
                MainTabView()
            } else {
                OnboardingView()
            }
        }
        .task { await model.refresh() }
    }
}
