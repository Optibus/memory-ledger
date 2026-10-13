"""The to-do detector in the capture hook (hooks/_capture.py, `detect_tasks`).

    python -m pytest tests/test_task_detection.py

What it guards: a task is created only from a personal to-do cue, never from an
instruction to the assistant. "I want to know how X works" is a question for
the assistant; "I need to send the recap" is a to-do. The cue list is small on
purpose, and this file is the list.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))

import _capture as C  # noqa: E402


@pytest.mark.parametrize("prompt", [
    "I need to send the recap document for the Wednesday sync.",
    "I have to review the priorities doc before Friday.",
    "Remind me to ping infra about the signing certificates.",
    "Don't forget the budget alert for the new cluster.",
    "TODO: move the secrets to AWS.",
    "Action item: add the no-concurrency decision link to the HLD page.",
    "Follow up with Gal on the KB pages.",
    "Make sure to open the infra ticket for the GitOps repo.",
    "Note to self: the export flow goes on the ticket for review.",
], ids=["need", "have", "remind", "forget", "todo", "action-item", "follow-up",
        "make-sure-to", "note-to-self"])
def test_a_personal_to_do_cue_makes_a_task(prompt):
    assert C.detect_tasks(prompt), prompt


@pytest.mark.parametrize("prompt", [
    "I want to know how I can use the ledger to improve my routine.",
    "I want to split the content of the dashboard into two apps.",
    "Make sure that his PRs follow the intake flow, and list the ones that do not.",
    "<pasted_content id=\"6ee7\"> Goal: I want to split the content of the app.",
    "What do I need to check with him so I have the full picture?",
    "Give me ideas of how my day to day work can be registered.",
    "Fix the export timeout in the integrations repo.",
], ids=["want-to-know", "want-to-split", "make-sure-that", "pasted", "question",
        "give-me", "imperative"])
def test_an_instruction_to_the_assistant_does_not(prompt):
    assert C.detect_tasks(prompt) == [], prompt


def test_one_task_per_sentence_and_no_duplicates():
    prompt = ("I need to send the recap. I need to send the recap. "
              "Also I have to book the room.")
    titles = C.detect_tasks(prompt)
    assert len(titles) == 2
    assert all(len(t) <= 90 for t in titles)
