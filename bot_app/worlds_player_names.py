"""Player labels shared by Worlds stat tables and damage charts."""


def player_name(name, series, team_id):
    """Remove only the player's own published team prefix, preserving multiword names."""
    name = str(name).strip()
    try:
        index = series["teams"].index(team_id)
    except ValueError:
        return name
    prefixes = {series["names"][index], series.get("team_names", series["names"])[index]}
    for prefix in sorted(prefixes, key=len, reverse=True):
        if prefix and name.casefold().startswith(prefix.casefold() + " "):
            return name[len(prefix):].strip()
    return name
