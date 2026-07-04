// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
//
// The Circuit: the world tour of the MCAT. Each section is a city with a room
// — New York's Emerald Room, Monte Carlo's Salon Bleu, Havana's Casa Verde,
// Macau's Jade House — and each topic is a table you win by application
// (quizzes + open-ended), never by how a flashcard felt. Tables carry a state
// (won / open / low table / roped off), a comprehension percentage, and a
// confidence band that widens where you are overconfident.

import SwiftUI

struct AtlasView: View {
    @EnvironmentObject private var model: AppModel

    private let columns = [GridItem(.adaptive(minimum: 150), spacing: AnSpace.sm)]

    var body: some View {
        AnScreen(issue: "The Circuit · won, not covered") {
            hero
            legend
            if model.mastery.isEmpty {
                Text(model.isLoading ? "Lighting the rooms…" : "No tables listed yet — win application hands and the Circuit fills in.")
                    .anNote(color: .anMuted)
                    .anPanel(fill: .anPanel2, stroke: .anRule)
            } else {
                ForEach(orderedSections, id: \.self) { section in
                    sectionView(section)
                }
            }
        }
        .task { await model.refresh() }
    }

    // MARK: Hero

    private var hero: some View {
        let scores = model.scores
        return VStack(alignment: .leading, spacing: AnSpace.md) {
            HStack(alignment: .firstTextBaseline, spacing: AnSpace.sm) {
                Text(percent(scores?.overallComprehension))
                    .font(.system(size: 48, weight: .heavy, design: .serif))
                    .monospacedDigit()
                    .foregroundStyle(Color.anInk)
                Text("of the tour won").anMicroLabel(color: .anMuted, size: 11)
            }
            Text("Your standing on the Circuit — measured only from what you can do at the tables, never from how a flashcard felt.")
                .font(.system(size: 14, design: .serif))
                .foregroundStyle(Color.anMuted)
                .lineSpacing(3)
            HStack(alignment: .top, spacing: AnSpace.xl) {
                StatBlock(value: percent(scores?.evidencedFraction), caption: "of exam tested")
                StatBlock(value: percent(scores?.memory.value), caption: "recall (memory)")
                if let trust = scores?.selfTrust {
                    StatBlock(
                        value: "\(trust)",
                        caption: "your tell /100",
                        tint: trust >= 70 ? .anInk : .anSignal
                    )
                }
            }
        }
        .anPanel(fill: .anPanel2, stroke: .anRule, padding: 18)
    }

    // MARK: Legend

    private var legend: some View {
        HStack(spacing: AnSpace.md) {
            ForEach(MasteryStatus.allCases) { status in
                HStack(spacing: 6) {
                    Rectangle()
                        .fill(status.accent)
                        .frame(width: 12, height: 12)
                    Text(status.tableLabel).anMicroLabel(color: .anMuted, size: 9.5)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Cities

    private var orderedSections: [MCATSection] {
        MCATSection.tourOrder.filter { model.masteryBySection[$0]?.isEmpty == false }
    }

    private func sectionView(_ section: MCATSection) -> some View {
        let topics = (model.masteryBySection[section] ?? [])
            .sorted { $0.examWeight > $1.examWeight }
        let wonCount = topics.filter { $0.status == .mastered }.count
        return VStack(alignment: .leading, spacing: AnSpace.md) {
            SectionHeader(
                number: section.code,
                title: "\(section.city) — \(section.room)",
                meta: "\(wonCount)/\(topics.count) won"
            )
            Text(section.flavor)
                .anNote(color: .anFaint, size: 11)
                .padding(.top, -AnSpace.sm)
            LazyVGrid(columns: columns, spacing: AnSpace.sm) {
                ForEach(topics) { topic in
                    AtlasCell(topic: topic)
                }
            }
        }
    }

    private func percent(_ value: Double?) -> String {
        guard let value else { return "—" }
        return "\(Int((value * 100).rounded()))%"
    }
}

// MARK: - Table tile

private struct AtlasCell: View {
    var topic: TopicMastery

    var body: some View {
        VStack(alignment: .leading, spacing: AnSpace.sm) {
            HStack(alignment: .top) {
                Text(topic.displayName)
                    .font(.system(size: 14.5, weight: .bold, design: .serif))
                    .foregroundStyle(Color.anInk)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 4)
                ChipStack(count: Int((topic.examWeight * 25).rounded()) + 1)
            }
            Spacer(minLength: 0)
            AnMeter(
                fraction: topic.comprehension ?? 0,
                bandLower: topic.bandLower,
                bandUpper: topic.bandUpper,
                fill: barColor,
                track: .anPanel,
                height: 8
            )
            HStack(alignment: .firstTextBaseline) {
                Text(percentText)
                    .font(.system(size: 16, weight: .heavy, design: .serif))
                    .monospacedDigit()
                    .foregroundStyle(topic.hasEvidence ? Color.anInk : Color.anFaint)
                Spacer()
                Text(topic.hasEvidence ? topic.status.tableLabel : "unlisted")
                    .anMicroLabel(color: .anMuted, size: 8.5)
            }
        }
        .padding(11)
        .frame(maxWidth: .infinity, minHeight: 100, alignment: .topLeading)
        .background(Color.anPanel)
        .overlay(alignment: .leading) {
            Rectangle().fill(topic.status.accent).frame(width: 4)
        }
        .overlay(
            RoundedRectangle(cornerRadius: AnSpace.radius)
                .strokeBorder(Color.anRule, lineWidth: 1)
        )
        .opacity(topic.status == .locked ? 0.55 : 1)
    }

    private var barColor: Color {
        switch topic.status {
        case .mastered: return .anGood
        case .corrective: return .anSignal
        default: return .anBrass
        }
    }

    private var percentText: String {
        guard let comprehension = topic.comprehension, topic.hasEvidence else { return "—" }
        return "\(Int((comprehension * 100).rounded()))%"
    }
}
