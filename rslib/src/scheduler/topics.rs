// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Speedrun topic-weighting helpers.
//!
//! Topics are encoded as note tags under a configurable prefix (default
//! `mcat::`), e.g. `mcat::bio_biochem::amino_acids`. The first path component
//! after the prefix is treated as the exam *section*, and each section carries
//! a relative exam weight reflecting how heavily it is tested. These weights
//! feed both the points-at-stake review order and the per-topic mastery rollup.
//!
//! Weights are deliberately conservative, relative multipliers centred on 1.0
//! (neutral). They are not a claim about exact AAMC percentages; they simply
//! bias scheduling toward higher-yield material. A card with no recognised
//! topic tag gets the neutral weight, so the ordering degrades gracefully on an
//! untagged deck.

use std::collections::HashMap;
use std::collections::HashSet;

use anki_proto::scheduler::GetTopicMasteryRequest;
use anki_proto::scheduler::GetTopicMasteryResponse;
use anki_proto::scheduler::TopicMastery;

use crate::prelude::*;
use crate::search::SortMode;

/// Default tag prefix that marks a tag as a Speedrun topic.
pub const DEFAULT_TOPIC_PREFIX: &str = "mcat::";

/// Bucket name for cards that carry no topic tag.
pub const UNTAGGED: &str = "(untagged)";

/// Default retrievability at or above which a studied card counts as mastered.
pub const DEFAULT_MASTERY_THRESHOLD: f64 = 0.9;

/// Neutral weight used for untagged cards / unknown sections.
pub const NEUTRAL_WEIGHT: f64 = 1.0;

/// Relative exam weight for a known MCAT section identifier (the first path
/// component after the topic prefix). Unknown sections fall back to the neutral
/// weight.
pub fn section_weight(section: &str) -> f64 {
    match section {
        // Large memorisable fact base; highest yield per card.
        "bio_biochem" => 1.30,
        // Substantial content base spanning psychology and sociology.
        "psych_soc" => 1.20,
        // Mixed reasoning + content.
        "chem_phys" => 1.10,
        // Skills section: almost no facts to memorise, so flashcards are
        // worth less here.
        "cars" => 0.80,
        _ => NEUTRAL_WEIGHT,
    }
}

/// Returns the topic tags (those starting with `prefix`) present in a raw,
/// whitespace-separated Anki tag string.
pub fn topics_from_tags<'a>(tags: &'a str, prefix: &str) -> Vec<&'a str> {
    tags.split_whitespace()
        .filter(|t| t.len() > prefix.len() && t.starts_with(prefix))
        .collect()
}

/// Exam weight for a single topic tag, derived from its section component.
pub fn topic_weight(topic: &str, prefix: &str) -> f64 {
    let Some(rest) = topic.strip_prefix(prefix) else {
        return NEUTRAL_WEIGHT;
    };
    let section = rest.split("::").next().unwrap_or("");
    section_weight(section)
}

/// The exam weight to assign to a card given its raw tag string. A card may sit
/// under several topics; we take the highest weight so the most valuable topic
/// drives its priority. Cards with no topic tag get the neutral weight.
pub fn card_weight_from_tags(tags: &str, prefix: &str) -> f64 {
    topics_from_tags(tags, prefix)
        .into_iter()
        .map(|t| topic_weight(t, prefix))
        .fold(None, |acc: Option<f64>, w| {
            Some(acc.map_or(w, |a| a.max(w)))
        })
        .unwrap_or(NEUTRAL_WEIGHT)
}

#[derive(Default)]
struct TopicAcc {
    total: u32,
    studied: u32,
    mastered: u32,
    recall_sum: f64,
}

impl Collection {
    /// Speedrun: per-topic mastery rollup powering the dashboard. Groups cards
    /// by tag prefix and reports, per topic, how many cards are mastered and
    /// the average recall. Runs in a single pass over the cards table.
    pub fn get_topic_mastery(
        &mut self,
        input: GetTopicMasteryRequest,
    ) -> Result<GetTopicMasteryResponse> {
        let prefix = if input.topic_prefix.is_empty() {
            DEFAULT_TOPIC_PREFIX.to_string()
        } else {
            input.topic_prefix.clone()
        };
        let threshold = if input.mastery_threshold <= 0.0 {
            DEFAULT_MASTERY_THRESHOLD
        } else {
            input.mastery_threshold
        };
        let timing = self.timing_today()?;
        let restrict: Option<HashSet<CardId>> = if input.search.trim().is_empty() {
            None
        } else {
            Some(
                self.search_cards(&input.search, SortMode::NoOrder)?
                    .into_iter()
                    .collect(),
            )
        };
        let rows = self.storage.topic_mastery_rows(timing)?;

        let mut map: HashMap<String, TopicAcc> = HashMap::new();
        let mut considered: u32 = 0;
        for (cid, tags, retrievability) in rows {
            if let Some(set) = &restrict {
                if !set.contains(&cid) {
                    continue;
                }
            }
            considered += 1;
            let topic_keys: Vec<String> = {
                let found = topics_from_tags(&tags, &prefix);
                if found.is_empty() {
                    vec![UNTAGGED.to_string()]
                } else {
                    found.into_iter().map(str::to_string).collect()
                }
            };
            for key in topic_keys {
                let entry = map.entry(key).or_default();
                entry.total += 1;
                if let Some(r) = retrievability {
                    entry.studied += 1;
                    entry.recall_sum += r;
                    if r >= threshold {
                        entry.mastered += 1;
                    }
                }
            }
        }

        let mut topics: Vec<TopicMastery> = map
            .into_iter()
            .map(|(topic, acc)| {
                let weight = topic_weight(&topic, &prefix);
                let average_recall = if acc.studied > 0 {
                    acc.recall_sum / acc.studied as f64
                } else {
                    0.0
                };
                let coverage = if acc.total > 0 {
                    acc.studied as f64 / acc.total as f64
                } else {
                    0.0
                };
                TopicMastery {
                    topic,
                    weight,
                    total_cards: acc.total,
                    studied_cards: acc.studied,
                    mastered_cards: acc.mastered,
                    average_recall,
                    coverage,
                }
            })
            .collect();
        // Highest-yield first, then alphabetical for a stable order.
        topics.sort_by(|a, b| {
            b.weight
                .partial_cmp(&a.weight)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| a.topic.cmp(&b.topic))
        });

        let topic_count = topics.len() as u32;
        Ok(GetTopicMasteryResponse {
            topics,
            topic_count,
            total_cards: considered,
        })
    }
}

#[cfg(test)]
mod test {
    use super::*;

    #[test]
    fn topic_extraction_filters_by_prefix() {
        let tags = " leech mcat::bio_biochem::amino_acids mcat::cars marked ";
        let topics = topics_from_tags(tags, DEFAULT_TOPIC_PREFIX);
        assert_eq!(topics, vec!["mcat::bio_biochem::amino_acids", "mcat::cars"]);
        // bare prefix with no body is ignored
        assert!(topics_from_tags("mcat::", DEFAULT_TOPIC_PREFIX).is_empty());
    }

    #[test]
    fn section_weights_are_applied() {
        let p = DEFAULT_TOPIC_PREFIX;
        assert_eq!(topic_weight("mcat::bio_biochem::enzymes", p), 1.30);
        assert_eq!(topic_weight("mcat::cars", p), 0.80);
        // unknown section -> neutral
        assert_eq!(topic_weight("mcat::mystery::x", p), NEUTRAL_WEIGHT);
        // not a topic tag -> neutral
        assert_eq!(topic_weight("biology", p), NEUTRAL_WEIGHT);
    }

    #[test]
    fn card_weight_takes_the_maximum_topic() {
        let p = DEFAULT_TOPIC_PREFIX;
        // cars (0.8) + bio_biochem (1.3) -> 1.3
        assert_eq!(
            card_weight_from_tags("mcat::cars mcat::bio_biochem::x", p),
            1.30
        );
        // no topic tag -> neutral
        assert_eq!(card_weight_from_tags("leech marked", p), NEUTRAL_WEIGHT);
    }

    #[test]
    fn topic_mastery_groups_and_weights() -> Result<()> {
        use anki_proto::scheduler::GetTopicMasteryRequest;

        let mut col = Collection::new();
        let deck = col.get_or_create_normal_deck("Default").unwrap();
        let nt = col.get_notetype_by_name("Basic").unwrap().unwrap();
        for topic in ["mcat::bio_biochem::a", "mcat::bio_biochem::b", "mcat::cars"] {
            let mut note = nt.new_note();
            note.set_field(0, "foo").unwrap();
            note.tags = vec![topic.to_string()];
            note.id.0 = 0;
            col.add_note(&mut note, deck.id).unwrap();
        }

        let resp = col.get_topic_mastery(GetTopicMasteryRequest::default())?;
        // two distinct bio leaf topics + one cars topic
        assert_eq!(resp.topic_count, 3);
        assert_eq!(resp.total_cards, 3);

        let bio = resp
            .topics
            .iter()
            .find(|t| t.topic == "mcat::bio_biochem::a")
            .unwrap();
        assert_eq!(bio.weight, 1.30);
        assert_eq!(bio.total_cards, 1);
        // newly added cards have no FSRS memory state yet
        assert_eq!(bio.studied_cards, 0);
        assert_eq!(bio.coverage, 0.0);

        // highest-weight topic sorts first
        assert!(resp.topics[0].weight >= resp.topics.last().unwrap().weight);

        Ok(())
    }
}
