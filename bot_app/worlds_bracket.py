"""Render the current Worlds Swiss or knockout bracket as a Discord-ready PNG."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import matplotlib
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from .lolesports import fetch_team_logo

GOLD = "#c8aa6e"
TEXT = "#eeeeee"
MUTED = "#999999"
KNOCKOUT_STAGES = {"quarterfinals": 0, "semifinals": 1, "finals": 2}


def current_bracket_series(series_list):
    """Scope both stage selection and rendering to the latest Worlds tournament."""
    eligible = [s for s in series_list if s["stage"].lower() in {"swiss", *KNOCKOUT_STAGES}]
    if not eligible:
        raise ValueError("No Worlds bracket available.")
    tournament = max(eligible, key=lambda s: s["start"]).get("tournament", "")
    return [s for s in eligible if s.get("tournament", "") == tournament]


def bracket_kind(series_list, *, now=None):
    """Switch after Swiss finishes or a knockout starts; future fixtures alone do not switch."""
    series_list = current_bracket_series(series_list)
    knockout = [s for s in series_list if s["stage"].lower() in KNOCKOUT_STAGES]
    if not knockout:
        return "Swiss"
    now = now or datetime.now(timezone.utc)
    if any(s["state"] != "unstarted" or s["start"] <= now.timestamp() for s in knockout):
        return "Knockout"
    swiss = [s for s in series_list if s["stage"].lower() == "swiss"]
    if swiss and all(s["state"] == "completed" for s in swiss):
        state = bracket_state(swiss)
        if sum(r[0] == 3 for r in state["records"].values()) == 8:
            return "Knockout"
    return "Swiss"


def knockout_state(series_list):
    """Keep published knockout fixtures; unassigned participants remain TBD."""
    series_list = current_bracket_series(series_list)
    rounds = []
    teams = {}
    for stage, expected in zip(KNOCKOUT_STAGES, (4, 2, 1)):
        matches = sorted((s for s in series_list if s["stage"].lower() == stage),
                         key=lambda s: (s["start"], s["id"]))
        if len(matches) > expected:
            raise ValueError("Inconsistent Worlds knockout fixtures.")
        rounds.append(matches + [None] * (expected - len(matches)))
        for match in matches:
            for i, team in enumerate(match["teams"]):
                if team == "0" or match["names"][i].upper() == "TBD":
                    continue
                teams[team] = {"name": match.get("team_names", match["names"])[i],
                               "image": match.get("images", ["", ""])[i]}
    # Align known upstream matches beneath their published next-round participants.
    # Without a published pairing, do not claim a winner's destination.
    for index in (1, 0):
        previous = [match for match in rounds[index] if match]
        ordered = []
        for target in rounds[index + 1]:
            linked = []
            if target:
                for team in target["teams"]:
                    if team != "0":
                        source = next((m for m in previous if team in m["teams"] and m not in ordered + linked), None)
                        if source:
                            linked.append(source)
            for match in previous:
                if len(linked) >= 2:
                    break
                if match not in ordered + linked:
                    linked.append(match)
            ordered.extend(linked + [None] * (2 - len(linked)))
        rounds[index] = ordered
    return {"rounds": rounds, "teams": teams}


def render_knockout(state, logos=None):
    """Draw four quarterfinals, two semifinals, and a final in the reference style."""
    logos = logos or {}
    image = Image.new("RGB", (1640, 850), "#080808")
    draw = ImageDraw.Draw(image)
    font_dir = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    regular = ImageFont.truetype(str(font_dir / "DejaVuSans.ttf"), 20)
    bold = ImageFont.truetype(str(font_dir / "DejaVuSans-Bold.ttf"), 19)
    xs, ys = (45, 585, 1125), ((92, 280, 468, 656), (186, 562), (374,))
    for col in (0, 1):
        for index, match in enumerate(state["rounds"][col]):
            target = state["rounds"][col + 1][index // 2]
            shared = (set(match["teams"]) & set(target["teams"])) - {"0"} if match and target else set()
            unknown = not match or all(t == "0" for t in match["teams"])
            target_unknown = not target or all(t == "0" for t in target["teams"])
            if shared or (unknown and target_unknown):
                start, end = ys[col][index] + 73, ys[col + 1][index // 2] + 36 + (index % 2) * 73
                draw.line([(xs[col] + 390, start), (xs[col] + 465, start),
                           (xs[col] + 465, end), (xs[col + 1], end)], fill="#373737", width=2)
    for col, label in enumerate(("QUARTERFINALS", "SEMIFINALS", "FINALS")):
        x = xs[col]
        draw.rounded_rectangle((x, 29, x + 390, 74), radius=6, fill="#cccccc")
        draw.text((x + 18, 42), label, font=bold, fill="#222222")
        for index, match in enumerate(state["rounds"][col]):
            y = ys[col][index]
            draw.rounded_rectangle((x, y, x + 390, y + 146), radius=6,
                                   fill="#1d1d1d", outline=GOLD if match and match["state"] == "inProgress" else "#303030")
            draw.line((x, y + 73, x + 390, y + 73), fill="#292929")
            for side in range(2):
                top = y + 73 * side
                team = match["teams"][side] if match else "0"
                name = state["teams"].get(team, {}).get("name", "TBD")
                icon = logos.get(team)
                if icon:
                    image.paste(icon, (x + 24, top + 20), icon)
                else:
                    draw.ellipse((x + 24, top + 18, x + 60, top + 54), outline="#353a3c", width=2)
                while draw.textlength(name, font=regular) > 235 and len(name) > 1:
                    name = name[:-2] + "…"
                winner = match and match["state"] == "completed" and match["score"][side] > match["score"][1-side]
                if winner:
                    draw.rectangle((x + 1, top + 2, x + 5, top + 71), fill=GOLD)
                draw.text((x + 80, top + 25), name, fill=TEXT, font=regular)
                if match and team != "0":
                    draw.text((x + 354, top + 25), str(match["score"][side]), fill=TEXT, font=bold)
    output = BytesIO()
    image.save(output, "PNG")
    output.seek(0)
    return output


def bracket_state(series_list):
    """Reconstruct Swiss series records, requiring complete and consistent history."""
    swiss = [s for s in series_list if s["stage"].lower() == "swiss"]
    if not swiss:
        raise ValueError("No Worlds Swiss bracket available.")
    tournament = max(swiss, key=lambda s: s["start"]).get("tournament", "")
    swiss = sorted((s for s in swiss if s.get("tournament", "") == tournament),
                   key=lambda s: (s["start"], s["id"]))
    records = defaultdict(lambda: [0, 0])
    teams, groups = {}, defaultdict(list)
    for series in swiss:
        a, b = series["teams"]
        before = tuple(records[a])
        if before != tuple(records[b]) or max(before) >= 3 or sum(before) >= 5:
            raise ValueError("Worlds Swiss history is incomplete.")
        number = sum(before) + 1
        groups[(number, before)].append(series)
        for i, team in enumerate((a, b)):
            teams[team] = {"name": series.get("team_names", series["names"])[i],
                           "code": series["names"][i], "image": series.get("images", ["", ""])[i]}
        if series["state"] == "completed":
            winner = a if series["score"][0] > series["score"][1] else b
            loser = b if winner == a else a
            records[winner][0] += 1
            records[loser][1] += 1
    if len(groups[(1, (0, 0))]) != 8 or len(teams) != 16:
        raise ValueError("Worlds Swiss history is incomplete.")
    return {"groups": groups, "teams": teams, "records": dict(records),
            "year": datetime.fromtimestamp(swiss[0]["start"], timezone.utc).year}


def render_bracket(state, logos=None):
    """Draw round/record groups, confirmed scores, and eight slots per outcome."""
    logos = logos or {}
    image = Image.new("RGB", (2400, 1150), "#080808")
    draw = ImageDraw.Draw(image)
    font_dir = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    regular = ImageFont.truetype(str(font_dir / "DejaVuSans.ttf"), 16)
    bold = ImageFont.truetype(str(font_dir / "DejaVuSans-Bold.ttf"), 17)
    title = ImageFont.truetype(str(font_dir / "DejaVuSans-Bold.ttf"), 27)
    draw.text((36, 24), f"WORLDS {state['year']}  /  SWISS STAGE", fill=TEXT, font=title)
    draw.text((36, 1115), "LoL Esports  |  Series scores  |  Undrawn matchups: TBD", fill=MUTED, font=regular)

    def text(value, x, y, *, width=225, color=TEXT, font=regular):
        value = str(value)
        while draw.textlength(value, font=font) > width and len(value) > 1:
            value = value[:-2] + "…"
        draw.text((x, y), value, fill=color, font=font)

    def logo(team, x, y):
        icon = logos.get(team)
        if icon:
            image.paste(icon, (x, y), icon)
        else:
            draw.ellipse((x + 3, y + 3, x + 31, y + 31), outline="#454545", width=1)

    layout = {1: [((0, 0), 8)], 2: [((1, 0), 4), ((0, 1), 4)],
              3: [((2, 0), 2), ((1, 1), 4), ((0, 2), 2)],
              4: [((2, 1), 3), ((1, 2), 3)], 5: [((2, 2), 3)]}
    for round_number, sections in layout.items():
        x, y = 36 + (round_number - 1) * 390, 85
        for record, count in sections:
            draw.rounded_rectangle((x, y, x + 340, y + 30), radius=4, fill="#cccccc")
            draw.text((x + 12, y + 5), f"ROUND {round_number}", fill="#222222", font=bold)
            draw.text((x + 252, y + 5), f"{record[0]}W–{record[1]}L", fill="#333333", font=regular)
            y += 40
            matches = state["groups"].get((round_number, record), [])
            for index in range(count):
                match = matches[index] if index < len(matches) else None
                draw.rounded_rectangle((x, y, x + 340, y + 96), radius=6, fill="#1d1d1d", outline="#303030")
                for side in range(2):
                    row_y = y + side * 46
                    if match:
                        team = match["teams"][side]
                        name = state["teams"][team]["name"]
                        winner = match["state"] == "completed" and match["score"][side] > match["score"][1-side]
                        if winner:
                            draw.rectangle((x, row_y + 3, x + 4, row_y + 45), fill=GOLD)
                        logo(team, x + 12, row_y + 6)
                        text(name, x + 56, row_y + 14, width=235,
                             color=TEXT if winner or match["state"] != "completed" else MUTED)
                        text(match["score"][side], x + 308, row_y + 14, width=24, font=bold)
                    else:
                        text("TBD", x + 56, row_y + 14, color=MUTED)
                if match and match["state"] == "inProgress":
                    draw.rounded_rectangle((x, y, x + 340, y + 96), radius=6, outline=GOLD, width=2)
                y += 106
            y += 24

    x = 1986
    draw.rounded_rectangle((x, 85, 2364, 1090), radius=8, fill="#171717")
    for label, record_index, top in (("ADVANCES", 0, 106), ("ELIMINATED", 1, 598)):
        draw.text((x + 18, top), label, fill=TEXT, font=bold)
        selected = [team for team, record in state["records"].items() if record[record_index] == 3]
        selected.sort(key=lambda team: (state["records"][team][1-record_index], state["teams"][team]["name"]))
        for index in range(8):
            y = top + 36 + index * 53
            draw.rounded_rectangle((x + 16, y, 2348, y + 46), radius=5, outline="#303030")
            selected_index = index if record_index == 0 else index - (8 - len(selected))
            if 0 <= selected_index < len(selected):
                team = selected[selected_index]
                logo(team, x + 24, y + 6)
                text(state["teams"][team]["name"], x + 66, y + 13, width=187)
                wins, losses = state["records"][team]
                text(f"{wins}W–{losses}L", 2262, y + 13, width=82, font=bold)
            else:
                text("TBD", x + 66, y + 13, color=MUTED)
    output = BytesIO()
    image.save(output, "PNG")
    output.seek(0)
    return output


def build_bracket(series_list, *, kind=None):
    """Select the current stage and fetch optional logos outside the event loop."""
    series_list = current_bracket_series(series_list)
    kind = kind or bracket_kind(series_list)
    state = knockout_state(series_list) if kind == "Knockout" else bracket_state(
        [s for s in series_list if s.get("known_teams", True)])
    logos = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        teams = list(state["teams"])
        data = pool.map(fetch_team_logo, [state["teams"][team]["image"] for team in teams])
        for team, art in zip(teams, data):
            if not art:
                continue
            try:
                icon = Image.open(BytesIO(art)).convert("RGBA")
                icon.thumbnail((34, 34), Image.Resampling.LANCZOS)
                tile = Image.new("RGBA", (34, 34))
                tile.alpha_composite(icon, ((34-icon.width)//2, (34-icon.height)//2))
                logos[team] = tile
            except (OSError, ValueError, UnidentifiedImageError):
                continue
    return render_knockout(state, logos) if kind == "Knockout" else render_bracket(state, logos)
