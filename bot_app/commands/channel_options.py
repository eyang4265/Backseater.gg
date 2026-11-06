"""Shared Discord choices for public announcement-channel commands."""

import discord

CHANNEL_TYPES = [discord.OptionChoice(name="Tracked Players", value="players"),
                 discord.OptionChoice(name="Worlds", value="worlds"),
                 discord.OptionChoice(name="Both", value="both")]
