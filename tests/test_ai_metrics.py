from datetime import UTC, datetime

import pytest
from alter_domain.metrics import AISnapshot, evaluation_gates, recall_at_k


def data(**kwargs):
    return AISnapshot(
        measured_at=datetime.now(UTC),
        queued_jobs=0,
        oldest_wait_seconds=0,
        completed_conversations=0,
        failed_conversations=0,
        cancelled_conversations=0,
        active_memories=0,
        max_memories_per_agent=0,
        suppressed_sources=0,
        unknown_calls=0,
        database_connections=1,
        database_size_bytes=100,
        db_stats_reset=None,
        block_reads=0,
        block_hits=0,
        deadlocks=0,
        charged_usd=0,
        **kwargs,
    )


def test_empty_dataset_does_not_invent_success_or_recall():
    assert data().completion_rate is None
    assert recall_at_k(set(), []) is None
    assert recall_at_k({"a", "b"}, ["a", "a", "x"]) == 0.5
    with pytest.raises(ValueError):
        recall_at_k({"a"}, [], 0)


def test_synthetic_or_short_observation_never_selects_an_upgrade():
    local = data().model_copy(update={"max_memories_per_agent": 500, "oldest_wait_seconds": 900})
    assert set(evaluation_gates(local, recall=0.1, labeled_cases=100).values()) == {
        "insufficient_evidence"
    }
    production = local.model_copy(update={"environment": "production", "observation_days": 7})
    assert evaluation_gates(production, recall=0.1, labeled_cases=100) == {
        "memory_search": "evaluate",
        "job_queue": "evaluate",
    }
    assert (
        evaluation_gates(production, recall=0.1, labeled_cases=1)["memory_search"]
        == "insufficient_evidence"
    )
