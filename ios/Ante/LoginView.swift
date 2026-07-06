// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
//
// The door of the den — the phone's copy of the desktop's login gate
// (ante/web/den.html renderGate): a local email + name sign-in that seats the
// player. Identity only; it namespaces the seat and gates the door exactly
// like the desktop, and — as on the desktop — the actual collection sync is a
// separate concern (the Ledger's "Wire" panel). Shown after onboarding until
// a seat is signed in, and again after signing out.

import SwiftUI

struct DenLoginView: View {
    @EnvironmentObject private var model: AppModel

    @State private var email = ""
    @State private var name = ""

    private var emailReady: Bool {
        !email.trimmingCharacters(in: .whitespaces).isEmpty
    }

    var body: some View {
        ZStack {
            Color.anPaper.ignoresSafeArea()
            VStack(spacing: 0) {
                ScrollView {
                    VStack(alignment: .leading, spacing: AnSpace.lg) {
                        Wordmark(size: 28)
                        VStack(alignment: .leading, spacing: AnSpace.sm) {
                            Text("The door").anMicroLabel(color: .anFaint, size: 11)
                            Text("Sign in and take your seat")
                                .anHeading(size: 30)
                            Text(
                                "A members-only card den for the MCAT. Your opponent "
                                    + "is the House — the forgetting curve. Sign in and "
                                    + "take your seat."
                            )
                            .font(.system(size: 15, design: .serif))
                            .foregroundStyle(Color.anMuted)
                            .lineSpacing(3)
                        }
                        VStack(spacing: AnSpace.md) {
                            loginField(
                                "Email", text: $email, keyboard: .emailAddress
                            )
                            loginField("Name (optional)", text: $name)
                        }
                        .anPanel(fill: .anPanel, stroke: .anRuleStrong, padding: 18)
                        Text(
                            "Local, on this device. Your progress is namespaced per "
                                + "account."
                        )
                        .anNote(color: .anMuted, size: 12)
                    }
                    .padding(AnSpace.xl)
                }
                VStack(spacing: AnSpace.md) {
                    AnCTAButton(
                        lead: "Enter the den",
                        meta: "SIGN IN · TAKE YOUR SEAT",
                        symbol: "arrow.right",
                        action: enter
                    )
                    .disabled(!emailReady)
                    .opacity(emailReady ? 1 : 0.45)
                    Button {
                        model.skipAccount()
                    } label: {
                        Text("Just show me the den →")
                            .anMicroLabel(color: .anMuted, size: 11)
                            .underline()
                    }
                    .buttonStyle(.plain)
                }
                .padding(.horizontal, AnSpace.xl)
                .padding(.vertical, AnSpace.lg)
            }
        }
    }

    private func enter() {
        guard emailReady else { return }
        model.signInAccount(email: email, name: name)
    }

    private func loginField(
        _ label: String,
        text: Binding<String>,
        keyboard: UIKeyboardType = .default
    ) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).anMicroLabel(size: 9.5)
            TextField("", text: text)
                .keyboardType(keyboard)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .font(.system(size: 15, design: .monospaced))
                .foregroundStyle(Color.anInk)
                .padding(12)
                .background(Color.anPanel2)
                .overlay(
                    RoundedRectangle(cornerRadius: AnSpace.radius)
                        .strokeBorder(Color.anRule, lineWidth: 1)
                )
        }
    }
}
