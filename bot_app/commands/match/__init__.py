"""Match-analysis command group."""

from .match import MatchStatsCommands, setup as setup_match
from .jungleproximity import JungleProximityCommands, setup as setup_jungleproximity
from .laning import LaningCommands, setup as setup_laning
from .timeline import MatchCommands, setup as setup_timeline

__all__ = [
    "JungleProximityCommands",
    "LaningCommands",
    "MatchCommands",
    "MatchStatsCommands",
    "setup_jungleproximity",
    "setup_laning",
    "setup_match",
    "setup_timeline",
]
