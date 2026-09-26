"""Deterministic randomness that survives Python upgrades.

Only `random.Random.random()` has a documented cross-version guarantee: the same seed
yields the same sequence. `choice`, `randrange`, `shuffle` and `sample` sit on internals
that CPython is allowed to change between releases. The datasets and the bootstrap feed
numbers that the README quotes and that CI re-derives on 3.11, 3.12 and 3.13, so every
draw in this project goes through `random()` here.

Hashing goes through sha256, never the builtin `hash()`, which is salted per process.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable, Sequence
from typing import TypeVar

T = TypeVar("T")

_SEPARATOR = "\x1f"


def seed_from(*parts: object) -> int:
    """64-bit integer derived from the parts. Same parts, same integer, in any process."""
    material = _SEPARATOR.join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def unit_hash(*parts: object) -> float:
    """Stable float in [0, 1) derived from the parts."""
    return seed_from(*parts) / 2**64


class Rng:
    """Seeded generator whose every method is built on `random()` alone."""

    __slots__ = ("_random",)

    def __init__(self, *seed_parts: object) -> None:
        # Not cryptography: a fixed, reproducible sequence is the whole point here.
        self._random = random.Random(seed_from(*seed_parts))  # noqa: S311

    def random(self) -> float:
        return self._random.random()

    def stream(self) -> Callable[[], float]:
        """The bound `random()` itself, for hot loops (the bootstrap draws millions)."""
        return self._random.random

    def below(self, n: int) -> int:
        """Uniform integer in [0, n)."""
        if n <= 0:
            raise ValueError("n must be positive")
        return min(int(self._random.random() * n), n - 1)

    def choice(self, items: Sequence[T]) -> T:
        if not items:
            raise ValueError("cannot choose from an empty sequence")
        return items[self.below(len(items))]

    def chance(self, probability: float) -> bool:
        return self._random.random() < probability

    def shuffled(self, items: Sequence[T]) -> list[T]:
        """Fisher-Yates on a copy."""
        out = list(items)
        for i in range(len(out) - 1, 0, -1):
            j = self.below(i + 1)
            out[i], out[j] = out[j], out[i]
        return out

    def sample(self, items: Sequence[T], k: int) -> list[T]:
        if k < 0 or k > len(items):
            raise ValueError("sample size out of range")
        return self.shuffled(items)[:k]
