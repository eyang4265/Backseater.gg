"""Laning-phase counter-pick statistics: extraction, storage, and the rating model.

See ``PLAN.md`` at the repo root for the full design. Modules here stay split
along the same lines the plan lays out:

* :mod:`bot_app.lane_matchups.extract` — pure, no I/O, turns one match +
  timeline into per-lane rows.
* :mod:`bot_app.lane_matchups.model` — pure, no I/O, fits lane ratings and
  matchup effects from rows handed in.
* :mod:`bot_app.lane_matchups.collect` — the network-touching snowball
  harvester, a manually-triggered offline maintenance path only.
"""
