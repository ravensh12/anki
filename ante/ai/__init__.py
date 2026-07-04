# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Ante AI: card generation, quality checking and evaluation.

Provider-isolated (Anthropic Claude when ANTHROPIC_API_KEY + the SDK are present,
otherwise a deterministic offline provider) so the app always works with AI off.
Every generated card carries a traceable source, and nothing reaches a student
without passing the quality checker's pre-declared cutoff.
"""
