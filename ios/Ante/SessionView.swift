// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
//
// The Table — the scheduled game, dealt for you. It walks a stakes-ordered
// stack from the engine and interleaves an application hand every fourth step,
// so recall and transfer are tested together. Every card asks for your read
// BEFORE you flip (Check / Call / Raise); every question asks how sure you are
// BEFORE it's scored — because Raise-and-miss is exactly what should pull your
// line down.

import SwiftUI

/// One unit of the game: a recall card or an interleaved application hand.
enum SessionStep {
    case card(ReviewCard)
    case item(ApplicationItem)
}

struct SessionView: View {
    @EnvironmentObject private var model: AppModel

    private enum Phase {
        case overview
        case running
        case done
    }

    private enum ItemPhase {
        case choosing
        case locked
        case revealed
    }

    @State private var phase: Phase = .overview
    @State private var steps: [SessionStep] = []
    @State private var current = 0

    // Transient per-step state, reset on every advance.
    @State private var cardShowAnswer = false
    @State private var cardConfidence: Double?
    @State private var itemPhase: ItemPhase = .choosing
    @State private var itemChosen: Int?
    @State private var itemConfidence: Double?

    @State private var cardsDone = 0
    @State private var itemsDone = 0
    /// When the current step was dealt — real per-card timing for the revlog.
    @State private var stepStartedAt = Date()

    /// The pre-flip read, in the den's language (mirrors den.html tableReveal).
    private let preflip: [(title: String, sub: String, value: Double)] = [
        ("Check", "guessing", 0.2),
        ("Call", "fairly sure", 0.6),
        ("Raise", "know it", 0.9),
    ]
    /// The lock-in before an application hand is scored (mirrors riverConf).
    private let lockin: [(title: String, sub: String, value: Double)] = [
        ("Check", "unsure", 0.3),
        ("Call", "fairly", 0.6),
        ("Raise", "confident", 0.9),
    ]
    private let grades: [(name: String, accent: Color)] = [
        ("Again", .anSignal),
        ("Hard", .anMuted),
        ("Good", .anGood),
        ("Easy", .anOchre),
    ]

    var body: some View {
        AnScreen(issue: "The Table · dealt for you") {
            switch phase {
            case .overview: overview
            case .running: running
            case .done: doneView
            }
        }
        .task { await model.refresh() }
    }

    // MARK: Overview

    private var overview: some View {
        let hasWork = !model.due.cards.isEmpty || !model.due.items.isEmpty
        return VStack(alignment: .leading, spacing: AnSpace.lg) {
            SectionHeader(number: "♠", title: "Tonight's game", meta: "dealt, not chosen")
            HStack(alignment: .top, spacing: AnSpace.xl) {
                StatBlock(value: "\(model.due.dueCardCount)", caption: "cards on the felt")
                StatBlock(value: "\(model.due.dueItemCount)", caption: "hands to play")
                StatBlock(value: "\(model.plan.recommendedDailyMinutes)", caption: "min planned")
            }
            if hasWork {
                AnCTAButton(
                    lead: "Deal me in",
                    meta: "\(model.due.cards.count) cards + \(model.due.items.count) questions · stakes ordered",
                    symbol: "suit.spade.fill",
                    tone: .signal
                ) { begin() }
            } else {
                AnCTAButton(
                    lead: "The felt is clear",
                    meta: "come back at the next game",
                    symbol: "checkmark",
                    tone: .good
                ) { model.selectedTab = .today }
            }
            Text("Recall cards and application hands are interleaved in stakes order — weakest, highest-value first. The dealer picks; you never do.")
                .anNote(color: .anMuted, size: 12)
        }
    }

    // MARK: Running

    private var running: some View {
        VStack(alignment: .leading, spacing: AnSpace.lg) {
            SectionHeader(number: "♠", title: "At the table", meta: "\(min(current + 1, steps.count)) of \(steps.count)")
            AnMeter(
                fraction: steps.isEmpty ? 0 : Double(current) / Double(steps.count),
                fill: .anBrass,
                height: 6
            )
            stepContent
            HStack {
                Text("cards \(cardsDone) · hands \(itemsDone)").anMicroLabel(color: .anFaint, size: 10)
                Spacer()
                Button {
                    endSession()
                } label: {
                    Text("Leave the table").anMicroLabel(color: .anMuted, size: 10.5)
                }
                .buttonStyle(.plain)
            }
        }
    }

    @ViewBuilder
    private var stepContent: some View {
        if current < steps.count {
            switch steps[current] {
            case .card(let card): cardStage(card)
            case .item(let item): itemStage(item)
            }
        }
    }

    // MARK: Card stage (a dealt card: cream face, card ink)

    private func cardStage(_ card: ReviewCard) -> some View {
        VStack(alignment: .leading, spacing: AnSpace.md) {
            HStack {
                Text(TopicFormat.nice(card.topic)).anMicroLabel(color: .anSignal, size: 10.5)
                Spacer()
                ChipStack(count: 3)
            }
            Text(card.question)
                .font(.system(size: 22, design: .serif))
                .foregroundStyle(Color.anCardInk)
                .lineSpacing(3)
            if !cardShowAnswer {
                Text("Your read, before the flip:")
                    .anMicroLabel(color: .anCardInk.opacity(0.55), size: 10.5)
                HStack(spacing: AnSpace.sm) {
                    ForEach(preflip, id: \.title) { option in
                        confidenceButton(option.title, option.sub) {
                            cardConfidence = option.value
                            cardShowAnswer = true
                        }
                    }
                }
            } else {
                Rectangle().fill(Color.anCardInk.opacity(0.18)).frame(height: 1).padding(.vertical, AnSpace.xs)
                Text(card.answer)
                    .font(.system(size: 20, design: .serif))
                    .foregroundStyle(Color.anCardInk)
                    .lineSpacing(3)
                Text("You said \(feltFlash(cardConfidence)) — now grade the hand honestly. Raise-and-miss is what drops your line.")
                    .font(.system(size: 11.5, design: .monospaced))
                    .foregroundStyle(Color.anCardInk.opacity(0.6))
                    .lineSpacing(3)
                HStack(spacing: AnSpace.sm) {
                    ForEach(Array(grades.enumerated()), id: \.element.name) { rating, grade in
                        gradeButton(grade.name, grade.accent) {
                            answerCard(card, rating: rating)
                        }
                    }
                }
                Text("Again returns it to the shoe — the House rakes the chips for now.")
                    .font(.system(size: 10.5, design: .monospaced))
                    .foregroundStyle(Color.anCardInk.opacity(0.45))
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.anCard)
        .overlay(
            RoundedRectangle(cornerRadius: 6)
                .strokeBorder(Color.anBrass.opacity(0.5), lineWidth: 1)
        )
        .shadow(color: .black.opacity(0.5), radius: 18, y: 8)
    }

    // MARK: Item stage (an application hand, played on the felt)

    private func itemStage(_ item: ApplicationItem) -> some View {
        VStack(alignment: .leading, spacing: AnSpace.md) {
            HStack(spacing: 8) {
                if item.isRetest {
                    Text("win it back")
                        .font(.system(size: 9, weight: .bold, design: .monospaced))
                        .foregroundStyle(Color.anSignal)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 3)
                        .overlay(RoundedRectangle(cornerRadius: AnSpace.radius).strokeBorder(Color.anSignal, lineWidth: 1))
                }
                Text("\(TopicFormat.nice(item.topic)) · application").anMicroLabel(color: .anBrass, size: 11)
            }
            Text(item.stem)
                .font(.system(size: 20, design: .serif))
                .foregroundStyle(Color.anInk)
                .lineSpacing(3)
            VStack(spacing: AnSpace.sm) {
                ForEach(Array(item.choices.enumerated()), id: \.offset) { index, choice in
                    choiceRow(index, choice, item)
                }
            }
            if itemPhase == .locked {
                Text("Lock it in — how sure are you?").anMicroLabel(color: .anFaint, size: 10.5)
                HStack(spacing: AnSpace.sm) {
                    ForEach(lockin, id: \.title) { option in
                        feltConfidenceButton(option.title, option.sub) {
                            itemConfidence = option.value
                            itemPhase = .revealed
                        }
                    }
                }
            }
            if itemPhase == .revealed {
                itemFeedback(item)
            }
        }
        .anPanel(fill: .anPanel, stroke: .anRule, padding: 20)
    }

    private func choiceRow(_ index: Int, _ choice: String, _ item: ApplicationItem) -> some View {
        let colors = choiceColors(index, item)
        return Button {
            if itemPhase == .choosing {
                itemChosen = index
                itemPhase = .locked
            }
        } label: {
            HStack {
                Text(choice)
                    .font(.system(size: 16, design: .serif))
                    .foregroundStyle(Color.anInk)
                    .multilineTextAlignment(.leading)
                Spacer(minLength: 0)
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(colors.bg)
            .overlay(
                RoundedRectangle(cornerRadius: AnSpace.radius)
                    .strokeBorder(colors.border, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }

    private func itemFeedback(_ item: ApplicationItem) -> some View {
        let correct = itemChosen == item.correctIndex
        let overconfident = !correct && (itemConfidence ?? 0) >= 0.85
        let message = correct
            ? (item.isRetest ? "Re-proven — the table is yours again." : "Won — you applied it.")
            : "Not quite — recall isn't transfer. This table drops to the low table until you win it back."
        return VStack(alignment: .leading, spacing: AnSpace.sm) {
            Text(message)
                .font(.system(size: 15, design: .serif))
                .foregroundStyle(correct ? Color.anGood : Color.anSignal)
            HStack(spacing: 8) {
                Text("you said: \(feltQuiz(itemConfidence))").anMicroLabel(color: .anMuted, size: 9.5)
                if overconfident {
                    Text("raise-and-miss — drops your line").anMicroLabel(color: .anSignal, size: 9.5)
                }
            }
            Button {
                itemsDone += 1
                advance()
            } label: {
                Text("Next hand")
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .tracking(0.8)
                    .textCase(.uppercase)
                    .foregroundStyle(Color.anCardInk)
                    .padding(.vertical, 11)
                    .padding(.horizontal, 22)
                    .background(Color.anBrass)
            }
            .buttonStyle(.plain)
            .padding(.top, AnSpace.xs)
        }
    }

    // MARK: Done

    private var doneView: some View {
        VStack(alignment: .leading, spacing: AnSpace.md) {
            SectionHeader(number: "♠", title: "Game banked", meta: nil)
            Text("The felt is clear.").anHeading(size: 24)
            Text("\(cardsDone) cards and \(itemsDone) hands, in the order that bought the most chips. Come back at the next game — the dealer will have the stack ready.")
                .anNote(color: .anMuted)
            AnCTAButton(lead: "Back to Tonight", meta: "the next game is scheduled", symbol: "arrow.left", tone: .ghost) {
                phase = .overview
                model.selectedTab = .today
            }
        }
    }

    // MARK: Buttons

    /// Check/Call/Raise on a cream card face.
    private func confidenceButton(_ title: String, _ sub: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 3) {
                Text(title)
                    .font(.system(size: 15, weight: .semibold, design: .serif))
                    .foregroundStyle(Color.anCardInk)
                Text(sub)
                    .font(.system(size: 9.5, design: .monospaced))
                    .foregroundStyle(Color.anCardInk.opacity(0.55))
            }
            .padding(.vertical, 13)
            .frame(maxWidth: .infinity)
            .background(Color.anCard)
            .overlay(
                RoundedRectangle(cornerRadius: AnSpace.radius)
                    .strokeBorder(Color.anCardInk.opacity(0.5), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }

    /// Check/Call/Raise on the dark felt.
    private func feltConfidenceButton(_ title: String, _ sub: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 3) {
                Text(title)
                    .font(.system(size: 15, weight: .semibold, design: .serif))
                    .foregroundStyle(Color.anInk)
                Text(sub)
                    .font(.system(size: 9.5, design: .monospaced))
                    .foregroundStyle(Color.anFaint)
            }
            .padding(.vertical, 13)
            .frame(maxWidth: .infinity)
            .background(Color.anPanel2)
            .overlay(
                RoundedRectangle(cornerRadius: AnSpace.radius)
                    .strokeBorder(Color.anRuleStrong.opacity(0.35), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }

    private func gradeButton(_ name: String, _ accent: Color, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 0) {
                Text(name)
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .tracking(0.5)
                    .textCase(.uppercase)
                    .foregroundStyle(Color.anCardInk)
                    .padding(.vertical, 12)
                    .frame(maxWidth: .infinity)
                    .background(Color.anCard)
                Rectangle().fill(accent).frame(height: 3)
            }
            .overlay(
                RoundedRectangle(cornerRadius: AnSpace.radius)
                    .strokeBorder(Color.anCardInk.opacity(0.4), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }

    // MARK: Logic

    private func begin() {
        steps = Self.interleave(cards: model.due.cards, items: model.due.items)
        current = 0
        cardsDone = 0
        itemsDone = 0
        resetStepState()
        phase = steps.isEmpty ? .done : .running
    }

    /// Grade the hand through the real scheduler (a no-op on sample data),
    /// then move on. The revlog gets the true time-to-answer.
    private func answerCard(_ card: ReviewCard, rating: Int) {
        let elapsed = Int(Date().timeIntervalSince(stepStartedAt) * 1000)
        cardsDone += 1
        Task { await model.answerCard(id: card.id, rating: rating, millisecondsTaken: elapsed) }
        advance()
    }

    private func advance() {
        if current + 1 >= steps.count {
            phase = .done
            // Bank the game: push tonight's reviews to the den so the desktop's
            // Circuit (and anyone watching it) updates while the phone is warm.
            Task { await model.syncNow() }
        } else {
            current += 1
            resetStepState()
        }
    }

    private func endSession() {
        phase = .overview
        resetStepState()
    }

    private func resetStepState() {
        cardShowAnswer = false
        cardConfidence = nil
        itemPhase = .choosing
        itemChosen = nil
        itemConfidence = nil
        stepStartedAt = Date()
    }

    private func choiceColors(_ index: Int, _ item: ApplicationItem) -> (bg: Color, border: Color) {
        if itemPhase == .revealed {
            if index == item.correctIndex { return (Color.anGood.opacity(0.16), .anGood) }
            if index == itemChosen { return (Color.anSignal.opacity(0.16), .anSignal) }
            return (Color.anPanel2, .anRule)
        }
        if index == itemChosen { return (Color.anPanel2, .anBrass) }
        return (Color.anPanel2, .anRule)
    }

    private func feltFlash(_ confidence: Double?) -> String {
        guard let confidence else { return "—" }
        if confidence >= 0.85 { return "Raise" }
        if confidence >= 0.5 { return "Call" }
        return "Check"
    }

    private func feltQuiz(_ confidence: Double?) -> String {
        guard let confidence else { return "—" }
        if confidence >= 0.85 { return "Raise" }
        if confidence >= 0.5 { return "Call" }
        return "Check"
    }

    /// Build the play order: a card each step, but every 4th step is an
    /// application hand when one is available (mirrors the desktop interleave).
    static func interleave(cards: [ReviewCard], items: [ApplicationItem]) -> [SessionStep] {
        var steps: [SessionStep] = []
        var cardIndex = 0
        var itemIndex = 0
        var step = 0
        while cardIndex < cards.count || itemIndex < items.count {
            let cardsLeft = cardIndex < cards.count
            let itemsLeft = itemIndex < items.count
            let takeItem = itemsLeft && (step % AnConfig.interleaveEvery == AnConfig.interleaveEvery - 1 || !cardsLeft)
            if takeItem {
                steps.append(.item(items[itemIndex]))
                itemIndex += 1
            } else {
                steps.append(.card(cards[cardIndex]))
                cardIndex += 1
            }
            step += 1
        }
        return steps
    }
}
