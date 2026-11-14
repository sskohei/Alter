"""Deterministic pair ranking using only public interests and completed counts."""

import hashlib
from collections.abc import Iterable
from uuid import UUID


def pair_rank(
    ids: tuple[UUID, UUID],
    interests: tuple[Iterable[str], Iterable[str]],
    completed_count: int,
    seed: int,
) -> tuple[int, str]:
    low, high = sorted(ids)
    score = len(set(interests[0]) & set(interests[1]))
    score += (1 if completed_count == 0 else 0) - completed_count
    tie = hashlib.sha256(f"{seed}:{low}:{high}".encode()).hexdigest()
    # Lower rank wins; seed only breaks ties in the public-interest/history score.
    return -score, tie
