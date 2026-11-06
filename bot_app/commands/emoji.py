"""Public legend for player markers, match controls, and custom icons."""

from __future__ import annotations

import discord
from discord.ext import commands

from ..render import make_embed
from .shared import GUILD_IDS, log_command


def build_emoji_legend() -> discord.Embed:
    """Explain player markers, controls, Worlds team logos, and reaction subscriptions."""
    embed = make_embed("", title="Emoji Guide")
    sections = (
        ("Worlds alerts", (
            "1️⃣–9️⃣ / 🔟 — Today's series numbers on the 4 a.m. Pacific Worlds schedule.",
            "1., 2., … — Manual /worlds schedule order; no reaction subscriptions. /stats selects series by matchup.",
            "React to subscribe to that series' start ping; remove your reaction to opt out.",
            "Team logos before names identify Worlds teams; announcements use official team names.",
            "Team icon aliases: TLAW → TL, TES → TOP, GEN → GenG; other teams use their named icon. Missing icons show text only.",
        )),
        ("Players", (
            "⭐ — Tracked account or linked teammate.",
            "👥 — Saved partymate inferred from repeated same-team games.",
            "😢 — Tracked player with no known same-team companions; solo queue is not confirmed.",
            "🫂 — Exactly two players in the game would otherwise display ⭐.",
            "🤡 — Five known players, four known + one unknown in Ranked Flex, or an inferred five-stack; not a confirmed party roster.",
        )),
        ("Previous game", (
            "👤 — Previous ally, on your team again.",
            "😈 — Previous enemy, opposing you again.",
            "🔄 — Previous ally now opposing you, or previous enemy now allied.",
            "Relative to selected/tracked players' immediately previous observed game. Known-player markers take priority.",
        )),
        ("Streaks, Pentakills & Rank Changes", (
            "🔥 — At least three consecutive wins.",
            "🥶 — At least three consecutive losses.",
            "🖐️ — Pentakill in this match; appears alongside streak/rank icons.",
            "🎉 — Ranked division or tier promotion.",
            "📉 — Ranked division or tier demotion.",
            "Promotion/demotion replaces the streak icon. Mayhem and Clash omit streaks.",
        )),
        ("Display Menu & Items", (
            "🧑 — Player names.",
            "🏅 / 🏆 — Ranked standings; 🏆 selects Solo Rank in Flex games.",
            "📈 — Individual performance ratings.",
            "🎒 — Item builds.",
            "⭐ in the Display menu — Champion mastery.",
            "⬛ — Empty item slot. 🥾 — Boots when a custom icon is unavailable.",
        )),
        ("Charts", (
            "⚔️ — Damage dealt to champions.",
            "🪙 — Gold earned.",
            "📊 — Team gold difference.",
            "🌳 — Jungle proximity.",
            "🛡️ — Damage taken.",
            "💚 — Healing and shielding.",
            "👁️ — Vision score.",
        )),
        ("Team Colors & Game Icons", (
            "🔵 🔴 🟢 🟡 🟣 ⚪ 🟤 🟠 — Arena team colors matching the charts; placement is shown separately.",
            "Champion icons use original artwork, including Worlds player names; plain text when unavailable.",
            "Renata Glasc also uses the Renata champion icon in stats.",
            "Rank, item, rune, and summoner-spell icons represent the named game content.",
        )),
    )
    for title, lines in sections:
        embed.add_field(name=title, value="\n".join(lines), inline=False)
    return embed


class EmojiGuide(commands.Cog):
    """Expose the bot's emoji legend as a public command."""

    @discord.slash_command(
        guild_ids=GUILD_IDS, description="Explain the bot's emojis and player markers"
    )
    async def emoji(self, ctx: discord.ApplicationContext) -> None:
        """Send the emoji guide with no lookup or player selection required."""
        log_command(ctx)
        await ctx.respond(embed=build_emoji_legend())


def setup(bot: discord.Bot) -> None:
    """Register the public emoji guide."""
    bot.add_cog(EmojiGuide())
