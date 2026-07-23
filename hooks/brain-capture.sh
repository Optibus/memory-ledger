#!/usr/bin/env bash
# work-brain — always-on capture enforcement (UserPromptSubmit hook).
#
# Runs on EVERY user prompt in EVERY session where this plugin is enabled.
# When the brain is enabled (its home dir exists), it injects the standing
# rule: recall relevant context before answering, and persist the interaction
# as graph data afterwards — so questions and decisions are recallable later
# and working memory is offloaded to the machine.
#
# Enable/disable: the brain is "enabled" when its home dir exists
# ($WORK_BRAIN_HOME, else ~/.work_brain). Remove/rename that dir (or unset the
# var) to disable — the hook then no-ops silently.
#
# Contract: exit 0 and print JSON {"additionalContext": "..."} to inject
# context; exit 0 with no output to stay silent.

set -euo pipefail

HOME_DIR="${WORK_BRAIN_HOME:-$HOME/.work_brain}"

# brain disabled -> no-op (no output, success)
[ -d "$HOME_DIR" ] || exit 0

cat <<'JSON'
{"additionalContext": "[work-brain] Capture rule is ON (brain enabled). Every interaction must leave brain data so questions & decisions are recallable later — offload memory to the machine. (1) RECALL before answering: scripts/graph.py match-skills \"<situation>\" and search relevant nodes; build on prior context. (2) CAPTURE after: persist what matters via scripts/graph.py as >=1 node — an answered question -> a note (Q + the answer); a choice -> a decision (part-of hub_process); a to-do -> a task; a new person/project/ticket/channel -> its node. Search-dedupe first, link it (about / relates-to / assigned-to / part-of), and commit the ~/.work_brain git. Skip only pure chit-chat. See the plugin's CONVENTIONS.md."}
JSON
exit 0
