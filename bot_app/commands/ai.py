"""Owner-only command for asking an OpenAI model a question from Discord."""

from __future__ import annotations

import asyncio
import requests

import discord
from discord.ext import commands

from ..config import get_settings
from .shared import GUILD_IDS, log_command


class AICommands(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the instance."""
        self.bot = bot

    @discord.slash_command(
        guild_ids=GUILD_IDS,
        name="ask",
        description="Ask the AI a question (owner only)",
    )
    @commands.is_owner()
    @discord.option("prompt", description="What should I ask the AI?", max_length=4000)
    async def ask(self, ctx: discord.ApplicationContext, prompt: str) -> None:
        """Handle ask."""
        log_command(ctx, prompt_length=len(prompt))
        settings = get_settings()
        if not settings.openai_api_key:
            await ctx.respond(
                "OpenAI is not configured. Add `openai_api_key` to `json/secrets.json`.",
                ephemeral=True,
            )
            return

        await ctx.defer()
        try:

            def ask_openai() -> str:
                """Handle openai."""
                response = requests.post(
                    "https://api.openai.com/v1/responses",
                    headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                    json={"model": settings.openai_model, "input": prompt},
                    timeout=90,
                )
                response.raise_for_status()
                data = response.json()
                return "".join(
                    content.get("text", "")
                    for item in data.get("output", [])
                    if item.get("type") == "message"
                    for content in item.get("content", [])
                    if content.get("type") == "output_text"
                ).strip()

            answer = (
                await asyncio.to_thread(ask_openai)
                or "The AI returned an empty response."
            )
        except Exception:
            await ctx.followup.send(
                "I couldn't reach the AI right now. Check the bot logs.", ephemeral=True
            )
            return

        for start in range(0, len(answer), 2000):
            await ctx.followup.send(answer[start : start + 2000])


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(AICommands(bot))
