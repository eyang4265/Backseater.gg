"""Population baselines that make a player rating an absolute statement.

Version one of :mod:`bot_app.rating` standardised every metric *within the
lobby*: each of the ten players' values was turned into a z-score against
the other nine. That has three consequences it was never meant to have.

1. The composite is centred at zero by construction, so a lobby's mean score
   is always 5.0. It is arithmetically impossible for a game to be well
   played or badly played — only for someone to be above the others.
2. A score means nothing across matches. Carrying a weak lobby and coasting
   in a strong one land in the same place.
3. Where a metric only applies to two players — jungle CS diff, every
   bot-lane duo diff — the two-sample z-score is exactly ``±1`` no matter
   how large the gap is, so a jungler sixty monsters ahead scores identically
   to one monster ahead.

This module replaces that yardstick with a corpus one. It reads the running
``(patch, position, metric)`` sums that ``rating_baselines`` accumulates and
recovers a population mean and standard deviation, so a metric can be scored
against *how that role usually performs* rather than against the nine other
people in the game. Keying on position is also what removes the role bias:
a support's vision score is compared with other supports' vision scores,
never with a mid laner's.

Baselines are optional and degrade cleanly. A metric with too little data
falls back to the old lobby normalisation, so the rating works on a cold
database and improves as :mod:`bot_app.lane_matchups.collect` and ordinary
tracking fill the table in.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from .match_cache import MatchCache

LOGGER = logging.getLogger(__name__)


MIN_BASELINE_SAMPLES = 200
"""Player-samples a ``(position, metric)`` needs before its baseline is trusted.

Samples, not matches: every match contributes two samples per position, one
per team, so a hundred matches clear this for the box-score metrics. Below
the threshold the standard deviation is too noisy to divide by, and a bad
denominator is worse than the lobby fallback it would replace. Timeline
metrics fill in more slowly, since only harvested matches carry a timeline.
"""


MIN_BASELINE_SPREAD = 1e-9


@dataclass(frozen=True)
class Baseline:
    """A metric's population mean and spread for one role."""

    samples: int
    mean: float
    stdev: float

    @property
    def usable(self) -> bool:
        """Whether this baseline has enough samples and enough spread to divide by."""
        return self.samples >= MIN_BASELINE_SAMPLES and self.stdev > MIN_BASELINE_SPREAD


class BaselineTable:
    """Position/metric baselines, with an explicit "not enough data" answer."""

    def __init__(self, entries: dict[tuple[str, str], Baseline] | None = None) -> None:
        """Initialize the instance."""
        self._entries = dict(entries or {})

    def __bool__(self) -> bool:
        """Whether any baseline at all is stored."""
        return bool(self._entries)

    def __len__(self) -> int:
        """How many ``(position, metric)`` baselines are stored."""
        return len(self._entries)

    def lookup(self, position: str, metric: str) -> Baseline | None:
        """The usable baseline for one role's metric, or None to fall back."""
        entry = self._entries.get((position, metric))
        if entry is None or not entry.usable:
            return None
        return entry

    def covers(self, positions: Sequence[str], metric: str) -> bool:
        """Whether every one of ``positions`` has a usable baseline for ``metric``.

        :mod:`bot_app.rating` normalises a metric for the whole lobby from a
        single source, never half from the corpus and half from the lobby —
        those two scales are not comparable, and mixing them would make one
        player's z-score mean something different from the next player's.
        """
        return all(self.lookup(position, metric) is not None for position in positions)


EMPTY_BASELINES = BaselineTable()


def _baseline_from_sums(
    samples: int, total: float, total_sq: float
) -> Baseline | None:
    """Recover ``(mean, stdev)`` from running sums, or None if degenerate."""
    if samples < 2:
        return None
    mean = total / samples
    variance = total_sq / samples - mean * mean
    if variance < 0.0:
        # Catastrophic cancellation on a near-zero-variance metric; the
        # spread is zero for practical purposes either way.
        variance = 0.0
    return Baseline(samples=samples, mean=mean, stdev=variance**0.5)


def load_baselines(
    cache: "MatchCache", patches: Sequence[str] | None = None
) -> BaselineTable:
    """Pool the stored per-patch rows into one baseline per (position, metric).

    Rows are summed across the patch window rather than taken from the
    newest patch alone: a single patch rarely clears
    :data:`MIN_BASELINE_SAMPLES` for the timeline-derived metrics, and the
    distributions these describe (gold at ten minutes, vision score per
    minute) move slowly enough between patches that pooling costs far less
    accuracy than the thin-sample noise it avoids. The rolling window is
    enforced by :meth:`MatchCache.prune_lane_statistics`, so anything still
    stored is in scope.
    """
    pooled: dict[tuple[str, str], list[float]] = {}
    for row in cache.rating_baseline_stats(patches):
        key = (row.position, row.metric)
        running = pooled.setdefault(key, [0.0, 0.0, 0.0])
        running[0] += row.samples
        running[1] += row.sum_value
        running[2] += row.sum_value_sq

    entries: dict[tuple[str, str], Baseline] = {}
    for key, (samples, total, total_sq) in pooled.items():
        baseline = _baseline_from_sums(int(samples), total, total_sq)
        if baseline is not None:
            entries[key] = baseline
    LOGGER.info(
        "Loaded %d usable rating baselines from %d pooled (position, metric) rows",
        len(entries), len(pooled),
    )
    return BaselineTable(entries)


_default_table: BaselineTable | None = None
_default_lock = threading.Lock()


def default_baselines() -> BaselineTable:
    """The process-wide baseline table, loaded once from the shared match cache.

    Cached because rating a match is on the announcement hot path and the
    table is small, static between harvests, and identical for every match.
    Call :func:`reset_default_baselines` after a harvest to pick up new rows.
    """
    global _default_table
    with _default_lock:
        if _default_table is None:
            try:
                from .match_cache import get_match_cache

                _default_table = load_baselines(get_match_cache())
                LOGGER.debug("Loaded %d rating baselines", len(_default_table))
            except Exception as error:  # pragma: no cover - defensive
                LOGGER.warning("Could not load rating baselines: %s", error)
                _default_table = EMPTY_BASELINES
        return _default_table


def reset_default_baselines() -> None:
    """Drop the cached table so the next rating reloads it from the cache."""
    global _default_table
    with _default_lock:
        _default_table = None
