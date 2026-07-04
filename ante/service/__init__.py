# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Ante model/AI service (PRD 3.2).

A separate FastAPI service for AI card generation and model training/eval. It is
intentionally decoupled: the desktop app runs, reviews, and scores with this
service unreachable (AI off), because the memory/performance/readiness logic is
dependency-free and runs in-process. This service is online-only sugar.

FastAPI is an optional dependency (ante/service/requirements.txt); the core
package never imports it.
"""
