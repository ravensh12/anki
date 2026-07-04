# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Ante: the MCAT-specific layer built on top of Anki's engine.

This package holds the exam taxonomy, the coverage/readiness/performance models,
and the AI helpers. It is deliberately importable without Anki so the pure logic
(coverage, calibration, score mapping) can be unit-tested in isolation.
"""
