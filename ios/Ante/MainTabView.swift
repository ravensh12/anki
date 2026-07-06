// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
//
// The four surfaces of the companion — Tonight, The Circuit, The Table, The
// Ledger — plus the shared page scaffold (`AnScreen`) they all sit in: dark
// felt, a ruled masthead with the ANTE wordmark, and a scrolling column. Open
// Felt and the demo simulator live on the desktop; the phone is the nightly
// game, the map, and the verdict.

import SwiftUI

/// The four surfaces, used both as tab tags and for CTA routing.
enum AppTab: Hashable {
    case today
    case atlas
    case session
    case plan
}

struct MainTabView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        TabView(selection: $model.selectedTab) {
            TodayView()
                .tabItem { Label("Tonight", systemImage: "moon.stars") }
                .tag(AppTab.today)
            AtlasView()
                .tabItem { Label("Circuit", systemImage: "map") }
                .tag(AppTab.atlas)
            SessionView()
                .tabItem { Label("Table", systemImage: "suit.spade.fill") }
                .tag(AppTab.session)
            PlanView()
                .tabItem { Label("Ledger", systemImage: "book.closed") }
                .tag(AppTab.plan)
        }
        .tint(.anBrass)
    }
}

/// The shared den page: felt ground, ruled masthead, scrolling body.
struct AnScreen<Content: View>: View {
    var issue: String
    @ViewBuilder var content: () -> Content
    @EnvironmentObject private var model: AppModel

    var body: some View {
        ZStack {
            Color.anPaper.ignoresSafeArea()
            LinearGradient(
                colors: [Color.anPanel.opacity(0.9), Color.anPaper],
                startPoint: .top,
                endPoint: .center
            )
            .ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: AnSpace.lg) {
                    masthead
                    if model.usingSampleData {
                        sampleDataBadge
                    }
                    content()
                }
                .padding(.horizontal, AnSpace.lg)
                .padding(.bottom, 64)
            }
        }
    }

    private var masthead: some View {
        VStack(spacing: AnSpace.sm) {
            HStack(alignment: .bottom) {
                Wordmark(size: 24)
                Spacer(minLength: AnSpace.md)
                Text(issue)
                    .anMicroLabel(color: .anFaint, size: 10)
                    .multilineTextAlignment(.trailing)
            }
            Rectangle()
                .fill(Color.anRule)
                .frame(height: 2)
        }
        .padding(.top, AnSpace.sm)
    }

    /// Honest label shown whenever the live engine is unavailable and these
    /// numbers come from `MockEngine`. Never let mocked data read as real.
    private var sampleDataBadge: some View {
        HStack(spacing: AnSpace.sm) {
            Image(systemName: "exclamationmark.triangle.fill")
            Text("SAMPLE DATA — engine offline; these figures are illustrative, not a real reading")
                .anMicroLabel(color: .anSignal, size: 10)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .foregroundStyle(Color.anSignal)
        .padding(AnSpace.sm)
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay(
            Rectangle().stroke(Color.anSignal.opacity(0.5), lineWidth: 1)
        )
    }
}
