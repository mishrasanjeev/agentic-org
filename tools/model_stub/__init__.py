# SPDX-License-Identifier: Apache-2.0
"""OpenAI-compatible model stub for the local development stack.

Serves ``POST /v1/chat/completions`` (including tool calls) from record/replay
cassettes (``core.model_replay``) or from scripted turn sequences
(``core.test_doubles.scripted_model``), so the API can run agents with no
model credentials. See ``docs/quickstart-local.md``.
"""
