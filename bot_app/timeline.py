"""Analysis of a match's frame-by-frame timeline.

Timeline v5 gives exact coordinates only for the killer and victim on a
``CHAMPION_KILL`` event; everyone else's position has to be read off the
nearest ``participantFrame``, which lands roughly once a minute. "Solo kill"
is therefore the same approximation third-party trackers use: no assist
credit, and nobody else within :data:`SOLO_KILL_PROXIMITY` at the nearest
frame.

The previous implementation re-scanned every frame to find the nearest one,
and rebuilt that frame's position map, for each kill event — and repeated the
whole classification per player. Frames are indexed once here, looked up by
binary search, and classified once for the entire match.
"""

from __future__ import annotations

import logging
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger(__name__)


SOLO_KILL_PROXIMITY = 1400
_PROXIMITY_SQUARED = SOLO_KILL_PROXIMITY**2


LANE_DIFF_TOLERANCE_MS = 30_000


LANE_DIFF_INTERVAL_MS = 300_000


@dataclass(frozen=True)
class SoloKillStats:
    solo_kills: int = 0
    solo_deaths: int = 0


@dataclass(frozen=True)
class KillEvent:
    """One ``CHAMPION_KILL`` reduced to what a map plot needs."""

    timestamp: int
    killer_id: int | None
    victim_id: int | None
    x: float
    y: float
    assist_count: int = 0

    @property
    def minute(self) -> int:
        """Handle minute."""
        return self.timestamp // 60_000


@dataclass(frozen=True)
class LaneDiff:
    gold: int
    xp: int
    levels: float
    cs: int


OBJECTIVE_PROXIMITY = 2200


DEATH_COST_FLOOR = 0.3


@dataclass(frozen=True)
class BountyLedger:
    """Gold a player took off the enemy, and gold they handed back.

    ``given`` is time-weighted rather than a plain sum: see
    :func:`death_time_factor`. Both sides are gold, not death counts, which is
    what keeps a strategy built on cheap repeated deaths from being scored as
    if every one of them were a late-game shutdown.
    """

    earned: float = 0.0
    given: float = 0.0


def death_time_factor(timestamp: int, duration_ms: int) -> float:
    """How much a death at ``timestamp`` counts, from 0.3 early to 1.0 at the end.

    A death two minutes in costs the game very little no matter what the
    bounty says; the same bounty at minute 35 can be the game. Scaling by
    elapsed fraction is the cheapest way to say so, and it means a rating
    built on this never has to special-case early aggression.
    """
    if duration_ms <= 0:
        return 1.0
    elapsed = timestamp / duration_ms
    if elapsed <= 0:
        return DEATH_COST_FLOOR
    if elapsed >= 1:
        return 1.0
    return DEATH_COST_FLOOR + (1.0 - DEATH_COST_FLOOR) * elapsed


MAX_LEVEL = 18


TOP_LANE_MAX_LEVEL = 20


def _level_thresholds() -> list[int]:
    """Cumulative XP to reach each level, index 0 being level 1.

    Levelling costs 280 XP for the first level and 100 more for each one
    after, so the table is cheaper to build than to spell out. It runs to the
    highest cap any role has; :func:`level_at_xp` clamps to the caller's.
    """
    thresholds = [0]
    for level in range(1, TOP_LANE_MAX_LEVEL):
        thresholds.append(thresholds[-1] + 180 + 100 * level)
    return thresholds


_LEVEL_THRESHOLDS = _level_thresholds()


def max_level_for_position(position: str | None) -> int:
    """Top lane's level cap, or the usual one for every other role."""
    return TOP_LANE_MAX_LEVEL if (position or "").upper() == "TOP" else MAX_LEVEL


def level_at_xp(xp: int | None, max_level: int = MAX_LEVEL) -> float:
    """Champion level as a fraction: ``7.5`` is halfway from 7 to 8.

    A raw XP gap says nothing on its own — 500 XP is most of a level early and
    a third of one late — so comparing two players means putting each on this
    scale first and subtracting there. Pass ``max_level`` from
    :func:`max_level_for_position`; XP past the cap does not keep counting.
    """
    if not xp or xp <= 0:
        return 1.0
    if xp >= _LEVEL_THRESHOLDS[max_level - 1]:
        return float(max_level)
    index = bisect_right(_LEVEL_THRESHOLDS, xp) - 1
    base = _LEVEL_THRESHOLDS[index]
    span = _LEVEL_THRESHOLDS[index + 1] - base
    return index + 1 + (xp - base) / span


def format_diff(value: int) -> str:
    """Signed, comma-grouped number: ``"+312"`` / ``"-58"``."""
    return f"+{value:,}" if value >= 0 else f"{value:,}"


def format_levels(value: float) -> str:
    """Signed level gap to one decimal: ``"+2.5"`` / ``"-1.2"``."""
    rounded = round(value, 1)

    return "+0.0" if rounded == 0 else f"{rounded:+.1f}"


def format_lane_lines(series: list[tuple[int, "LaneDiff"]]) -> str:
    """One line per mark: ``"@10 min — Gold -321 | Levels -0.5 | CS -7"``.

    Plain text, so it reads in the embed's own font alongside everything
    else. Empty series give an empty string; the caller says what "no data"
    should read as.
    """
    return "\n".join(
        f"@{minute} min — Gold {format_diff(diff.gold)} | "
        f"Levels {format_levels(diff.levels)} | "
        f"CS {format_diff(diff.cs)}"
        for minute, diff in series
    )


def opponent_participant_id(
    match: dict[str, Any], participant: dict[str, Any]
) -> int | None:
    """The mirrored laner on the other team, or None (ARAM, Arena, no data)."""
    position = participant.get("teamPosition")
    if not position:
        return None
    team_id = participant.get("teamId")
    for other in match.get("info", {}).get("participants", []):
        if other.get("teamId") != team_id and other.get("teamPosition") == position:
            return other.get("participantId")
    return None


def participant_at_slot(
    match: dict[str, Any], slot: int | None
) -> dict[str, Any] | None:
    """The player in a 1-10 draft slot: 1-5 blue top→support, 6-10 red.

    Riot numbers participants in that order, so the slot is the
    ``participantId``. None when the match has no such slot.
    """
    if slot is None:
        return None
    for participant in match.get("info", {}).get("participants", []):
        if participant.get("participantId") == slot:
            return participant
    return None


def _cs(participant_frame: dict[str, Any]) -> int:
    """Handle cs."""
    return participant_frame.get("minionsKilled", 0) + participant_frame.get(
        "jungleMinionsKilled", 0
    )


class MatchTimeline:
    """Indexed view over one match's timeline payload."""

    def __init__(self, payload: dict[str, Any]) -> None:
        """Initialize the instance."""
        frames = payload.get("info", {}).get("frames", []) or []
        self._frames: list[dict[str, Any]] = sorted(
            frames, key=lambda f: f.get("timestamp", 0)
        )
        self._timestamps: list[int] = [
            frame.get("timestamp", 0) for frame in self._frames
        ]
        self._positions: list[dict[int, tuple[float, float]] | None] = [None] * len(
            self._frames
        )
        self._solo_kills: list[dict[str, Any]] | None = None
        self._champion_kills: list[KillEvent] | None = None
        self._all_events_cache: list[dict[str, Any]] | None = None
        self._raw_champion_kills_cache: list[dict[str, Any]] | None = None
        self._elite_monster_events_cache: list[dict[str, Any]] | None = None
        LOGGER.debug("Indexed timeline with %d frames", len(self._frames))

    def _frame_index_at_or_before(self, timestamp: int) -> int | None:
        """Handle index at or before."""
        index = bisect_right(self._timestamps, timestamp) - 1
        return index if index >= 0 else None

    def _nearest_frame_index(self, timestamp: int) -> int | None:
        """Handle frame index."""
        if not self._timestamps:
            return None
        index = bisect_left(self._timestamps, timestamp)
        if index == 0:
            return 0
        if index >= len(self._timestamps):
            return len(self._timestamps) - 1
        before, after = self._timestamps[index - 1], self._timestamps[index]
        return index - 1 if timestamp - before <= after - timestamp else index

    def _frame_positions(self, index: int) -> dict[int, tuple[float, float]]:
        """participantId -> (x, y) for one frame, built at most once."""
        cached = self._positions[index]
        if cached is None:
            cached = {}
            for key, participant_frame in (
                self._frames[index].get("participantFrames", {}).items()
            ):
                position = participant_frame.get("position")
                if position:
                    try:
                        cached[int(key)] = (position.get("x", 0), position.get("y", 0))
                    except (TypeError, ValueError):
                        continue
            self._positions[index] = cached
        return cached

    def _classified_solo_kills(self) -> list[dict[str, Any]]:
        """Every ``CHAMPION_KILL`` event that qualifies as a solo kill.

        Classified once for the whole match; both players' counts read from
        the same pass.
        """
        if self._solo_kills is None:
            self._solo_kills = [
                event
                for frame in self._frames
                for event in frame.get("events", [])
                if event.get("type") == "CHAMPION_KILL" and self._is_solo_kill(event)
            ]
        return self._solo_kills

    def _is_solo_kill(self, event: dict[str, Any]) -> bool:
        """Handle solo kill."""
        if event.get("assistingParticipantIds"):
            return False
        killer_id = event.get("killerId")
        victim_id = event.get("victimId")

        if not killer_id or not victim_id:
            return False

        position = event.get("position")
        if not position:
            return True

        index = self._frame_index_at_or_before(event.get("timestamp", 0))
        if index is None:
            return True

        kill_x, kill_y = position.get("x", 0), position.get("y", 0)
        for participant_id, (x, y) in self._frame_positions(index).items():
            if participant_id in (killer_id, victim_id):
                continue
            if (kill_x - x) ** 2 + (kill_y - y) ** 2 <= _PROXIMITY_SQUARED:
                return False
        return True

    def solo_kill_stats(self, participant_id: int | None) -> SoloKillStats:
        """Handle kill stats."""
        if participant_id is None:
            return SoloKillStats()
        events = self._classified_solo_kills()
        return SoloKillStats(
            solo_kills=sum(
                1 for event in events if event.get("killerId") == participant_id
            ),
            solo_deaths=sum(
                1 for event in events if event.get("victimId") == participant_id
            ),
        )

    def champion_kills(self) -> list[KillEvent]:
        """Every kill that carries a map position, oldest first.

        Kills without a position (rare, but the payload allows it) are dropped
        rather than plotted at the map's origin.
        """
        if self._champion_kills is None:
            events: list[KillEvent] = []
            for frame in self._frames:
                for event in frame.get("events", []):
                    if event.get("type") != "CHAMPION_KILL":
                        continue
                    position = event.get("position") or {}
                    x, y = position.get("x"), position.get("y")
                    if x is None or y is None:
                        continue
                    events.append(
                        KillEvent(
                            timestamp=event.get("timestamp", 0),
                            killer_id=event.get("killerId") or None,
                            victim_id=event.get("victimId") or None,
                            x=x,
                            y=y,
                            assist_count=len(
                                event.get("assistingParticipantIds") or ()
                            ),
                        )
                    )
            self._champion_kills = sorted(events, key=lambda event: event.timestamp)
        return self._champion_kills

    def kills_and_deaths(
        self, participant_id: int | None
    ) -> tuple[list[KillEvent], list[KillEvent]]:
        """One player's kills and deaths, each list oldest first."""
        if participant_id is None:
            return [], []
        events = self.champion_kills()
        return (
            [event for event in events if event.killer_id == participant_id],
            [event for event in events if event.victim_id == participant_id],
        )

    def lane_diff_at(
        self,
        participant_id: int | None,
        opponent_id: int | None,
        timestamp: int,
        max_level: int = MAX_LEVEL,
    ) -> LaneDiff | None:
        """Gold/level/CS advantage over the lane opponent at a point in the game.

        One ``max_level`` covers both players: the lane opponent mirrors the
        role, so they share a cap.

        None when that minute never happened (a 12-minute stomp asked for @15)
        or either side's frame data is missing.
        """
        if participant_id is None or opponent_id is None:
            return None

        index = self._nearest_frame_index(timestamp)
        if (
            index is None
            or abs(self._timestamps[index] - timestamp) > LANE_DIFF_TOLERANCE_MS
        ):
            return None

        participant_frames = self._frames[index].get("participantFrames", {})
        mine = participant_frames.get(str(participant_id))
        theirs = participant_frames.get(str(opponent_id))
        if mine is None or theirs is None:
            return None

        return LaneDiff(
            gold=mine.get("totalGold", 0) - theirs.get("totalGold", 0),
            xp=mine.get("xp", 0) - theirs.get("xp", 0),
            levels=(
                level_at_xp(mine.get("xp", 0), max_level)
                - level_at_xp(theirs.get("xp", 0), max_level)
            ),
            cs=_cs(mine) - _cs(theirs),
        )

    def stats_at(
        self, participant_id: int | None, timestamp: int
    ) -> dict[str, int] | None:
        """Raw gold/xp/cs for one participant at a point in the game, or None.

        Companion to :meth:`lane_diff_at` for callers that want both sides'
        absolute values (e.g. side-by-side embed columns) rather than only
        the signed difference.
        """
        if participant_id is None:
            return None

        index = self._nearest_frame_index(timestamp)
        if (
            index is None
            or abs(self._timestamps[index] - timestamp) > LANE_DIFF_TOLERANCE_MS
        ):
            return None

        frame = self._frames[index].get("participantFrames", {}).get(str(participant_id))
        if frame is None:
            return None

        return {
            "gold": frame.get("totalGold", 0),
            "xp": frame.get("xp", 0),
            "cs": _cs(frame),
        }

    def duration_ms(self) -> int:
        """Timestamp of the last frame — the game's length, to the nearest frame."""
        return self._timestamps[-1] if self._timestamps else 0

    def _raw_champion_kills(self) -> list[dict[str, Any]]:
        """Every ``CHAMPION_KILL`` event, positions optional.

        :meth:`champion_kills` drops events without coordinates because it
        feeds a map plot. Bounty accounting wants all of them.
        """
        if self._raw_champion_kills_cache is None:
            self._raw_champion_kills_cache = [
                event
                for frame in self._frames
                for event in frame.get("events", [])
                if event.get("type") == "CHAMPION_KILL"
            ]
        return self._raw_champion_kills_cache

    def bounty_ledger(self, participant_id: int | None) -> BountyLedger:
        """Kill gold one player took, and the time-weighted gold they gave back.

        Killers take the whole bounty; assists split an equal share with the
        killer, so a five-man collapse does not credit five players with the
        full amount. Deaths are weighted by :func:`death_time_factor`.
        """
        if participant_id is None:
            return BountyLedger()

        duration = self.duration_ms()
        earned = 0.0
        given = 0.0
        for event in self._raw_champion_kills():
            value = float(event.get("bounty") or 0) + float(
                event.get("shutdownBounty") or 0
            )
            if not value:
                continue
            assists = event.get("assistingParticipantIds") or ()
            if event.get("killerId") == participant_id:
                earned += value / (len(assists) + 1)
            elif participant_id in assists:
                earned += value / (len(assists) + 1)
            if event.get("victimId") == participant_id:
                given += value * death_time_factor(event.get("timestamp", 0), duration)
        return BountyLedger(earned=earned, given=given)

    def _all_events(self) -> list[dict[str, Any]]:
        """Handle events."""
        if self._all_events_cache is None:
            self._all_events_cache = [
                event for frame in self._frames for event in frame.get("events", [])
            ]
        return self._all_events_cache

    def _event_participation(self, participant_id: int | None, event_type: str) -> int:
        """Events of ``event_type`` this player was the killer or an explicit assistant on.

        Deliberately credit-only, not proximity-based: rating.md §2 draws a
        hard line against treating a nearby frame sample as proof of
        participation, so unlike :meth:`objective_presence` (used elsewhere
        for the jungle-proximity feature) this never falls back to position.
        """
        if participant_id is None:
            return 0
        return sum(
            1
            for event in self._all_events()
            if event.get("type") == event_type
            and (
                event.get("killerId") == participant_id
                or participant_id in (event.get("assistingParticipantIds") or ())
            )
        )

    def epic_event_participation(self, participant_id: int | None) -> int:
        """Epic-monster takedowns this player got explicit killer/assist credit for."""
        return self._event_participation(participant_id, "ELITE_MONSTER_KILL")

    def building_event_participation(self, participant_id: int | None) -> int:
        """Turret/inhibitor takedowns this player got explicit killer/assist credit for."""
        return self._event_participation(participant_id, "BUILDING_KILL")

    def jungle_cs_diff_at(
        self, participant_id: int | None, opponent_id: int | None, timestamp: int
    ) -> int | None:
        """Neutral-jungle-only CS advantage over the lane opponent at a mark.

        Separate from :meth:`lane_diff_at`'s ``cs`` field, which includes lane
        minions: two junglers' farm gap is a jungle-camp story, and mixing in
        lane CS (usually near zero for both) would only add noise.
        """
        if participant_id is None or opponent_id is None:
            return None
        index = self._nearest_frame_index(timestamp)
        if (
            index is None
            or abs(self._timestamps[index] - timestamp) > LANE_DIFF_TOLERANCE_MS
        ):
            return None
        participant_frames = self._frames[index].get("participantFrames", {})
        mine = participant_frames.get(str(participant_id))
        theirs = participant_frames.get(str(opponent_id))
        if mine is None or theirs is None:
            return None
        return mine.get("jungleMinionsKilled", 0) - theirs.get("jungleMinionsKilled", 0)

    def objective_presence(
        self,
        participant_id: int | None,
        team_participant_ids: frozenset[int] | None = None,
        radius: int = OBJECTIVE_PROXIMITY,
    ) -> int:
        """Team objectives this player was actually present for.

        Takedown credit in the post-game stats is generous — it counts anyone
        alive on the map. This counts an objective only when the player landed
        the blow, assisted, or stood within ``radius`` of it at the nearest
        frame, which is the difference between showing up for a Baron and
        being in the enemy bot lane while it happened.

        ``team_participant_ids`` restricts the count to objectives this
        player's team took; pass None to count every objective on the map.
        """
        if participant_id is None:
            return 0

        present = 0
        # This is intentionally narrower than general objective contribution:
        # the rating's presence metric concerns epic monsters only. Turrets
        # and plates are scored separately.
        if self._elite_monster_events_cache is None:
            self._elite_monster_events_cache = [
                event
                for frame in self._frames
                for event in frame.get("events", [])
                if event.get("type") == "ELITE_MONSTER_KILL"
            ]
        for event in self._elite_monster_events_cache:
            killer_id = event.get("killerId")
            if team_participant_ids is not None and (
                killer_id not in team_participant_ids
            ):
                continue
            assists = event.get("assistingParticipantIds") or ()
            if killer_id == participant_id or participant_id in assists:
                present += 1
                continue
            if self._within(participant_id, event, radius):
                present += 1
        return present

    def _within(
        self, participant_id: int, event: dict[str, Any], radius: int
    ) -> bool:
        """Whether the player stood within ``radius`` of an event's position."""
        position = event.get("position")
        if not position:
            return False
        index = self._nearest_frame_index(event.get("timestamp", 0))
        if index is None:
            return False
        location = self._frame_positions(index).get(participant_id)
        if location is None:
            return False
        dx = position.get("x", 0) - location[0]
        dy = position.get("y", 0) - location[1]
        return dx * dx + dy * dy <= radius**2

    def lane_diff_series(
        self,
        participant_id: int | None,
        opponent_id: int | None,
        interval_ms: int = LANE_DIFF_INTERVAL_MS,
        max_level: int = MAX_LEVEL,
    ) -> list[tuple[int, LaneDiff]]:
        """Lane diffs at every ``interval_ms`` mark, as ``(minute, diff)`` pairs.

        Walks the game rather than a fixed set of marks, so a 40-minute game
        reports eight of them and a 12-minute surrender reports two. Marks the
        game never reached are left out instead of repeating the final frame's
        numbers, and a mark whose frame data is missing is skipped rather than
        ending the series early.
        """
        if participant_id is None or opponent_id is None or interval_ms <= 0:
            return []

        series: list[tuple[int, LaneDiff]] = []
        last_mark = self.duration_ms() + LANE_DIFF_TOLERANCE_MS
        for timestamp in range(interval_ms, last_mark + 1, interval_ms):
            diff = self.lane_diff_at(participant_id, opponent_id, timestamp, max_level)
            if diff is not None:
                series.append((timestamp // 60_000, diff))
        LOGGER.debug(
            "Lane diff series for participant %s vs %s: %d marks",
            participant_id,
            opponent_id,
            len(series),
        )
        return series
