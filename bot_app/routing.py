"""Riot platform codes and the regional routing values they map to.

One table per platform replaces the previous membership tests against four
separate region lists, which had drifted out of sync with Riot's actual
platform codes: ``RU1``/``PH1``/``SG1``/``TW1``/``VN1``/``TH1`` are not
platforms (the real ones are ``RU``/``PH2``/``SG2``/``TW2``/``VN2``/``TH2``)
and ``OC1`` appeared in no list at all, so every one of those servers built
request URLs against the host ``None.api.riotgames.com``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Platform:
    code: str

    account_route: str

    match_route: str

    opgg_slug: str | None


_PLATFORMS: tuple[Platform, ...] = (
    Platform("BR1", "americas", "americas", "br"),
    Platform("EUN1", "europe", "europe", "eune"),
    Platform("EUW1", "europe", "europe", "euw"),
    Platform("JP1", "asia", "asia", "jp"),
    Platform("KR", "asia", "asia", "kr"),
    Platform("LA1", "americas", "americas", "lan"),
    Platform("LA2", "americas", "americas", "las"),
    Platform("ME1", "europe", "europe", "me"),
    Platform("NA1", "americas", "americas", "na"),
    Platform("OC1", "americas", "sea", "oce"),
    Platform("PBE1", "americas", "americas", None),
    Platform("PH2", "asia", "sea", "ph"),
    Platform("RU", "europe", "europe", "ru"),
    Platform("SG2", "asia", "sea", "sg"),
    Platform("TH2", "asia", "sea", "th"),
    Platform("TR1", "europe", "europe", "tr"),
    Platform("TW2", "asia", "sea", "tw"),
    Platform("VN2", "asia", "sea", "vn"),
)

PLATFORMS: dict[str, Platform] = {platform.code: platform for platform in _PLATFORMS}


SERVERS: list[str] = [platform.code for platform in _PLATFORMS]

DEFAULT_PLATFORM = "NA1"


def platform(code: str | None) -> Platform | None:
    """Handle platform."""
    if not code:
        return None
    return PLATFORMS.get(code.upper())


def account_route(code: str | None) -> str | None:
    """Regional routing value for account-v1, or None for an unknown platform."""
    found = platform(code)
    return found.account_route if found else None


def match_route(code: str | None) -> str | None:
    """Regional routing value for match-v5, or None for an unknown platform."""
    found = platform(code)
    return found.match_route if found else None


def opgg_url(code: str | None, riot_id: str | None) -> str | None:
    """Profile link for a ``Name#Tag`` riot id, or None when it can't be built."""
    from urllib.parse import quote

    found = platform(code)
    if found is None or found.opgg_slug is None or not riot_id or "#" not in riot_id:
        return None
    name, tag = riot_id.split("#", 1)
    return f"https://op.gg/lol/summoners/{found.opgg_slug}/{quote(name)}-{quote(tag)}"


def split_riot_id(
    summoner: str | None, tag: str | None
) -> tuple[str | None, str | None]:
    """Accept a combined ``Name#Tag`` in the summoner field.

    An explicitly supplied tag always wins over one embedded in the name.
    """
    if summoner and "#" in summoner and tag is None:
        name, _, tag_part = summoner.partition("#")
        return name, tag_part
    return summoner, tag
