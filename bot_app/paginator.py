"""Reusable Discord embed pagination that anyone can navigate."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

import discord

LOGGER = logging.getLogger(__name__)


class Paginator(discord.ui.View):
    def __init__(
        self,
        items: Sequence[Any],
        *,
        author_id: int,
        render_page: Callable[[Sequence[Any], int, int], discord.Embed],
        page_size: int = 10,
        timeout: float = 120,
    ) -> None:
        """Initialize the instance."""
        super().__init__(timeout=timeout)
        self.items = list(items)
        self.author_id = author_id
        self.render_page = render_page
        self.page_size = page_size
        self.page = 0
        self.max_page = max((len(self.items) - 1) // page_size, 0)
        self.message: Any | None = None
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        """Handle buttons."""
        at_start = self.page == 0
        at_end = self.page == self.max_page
        self.first_button.disabled = at_start
        self.previous_button.disabled = at_start
        self.next_button.disabled = at_end
        self.last_button.disabled = at_end

    def render(self) -> discord.Embed:
        """Render render."""
        start = self.page * self.page_size
        return self.render_page(
            self.items[start : start + self.page_size], self.page, self.max_page + 1
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Allow any reader to use the page controls."""
        return True

    async def on_timeout(self) -> None:
        """Handle timeout."""
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                LOGGER.debug("Could not disable a timed-out paginator", exc_info=True)

    async def _go_to(self, page: int, interaction: discord.Interaction) -> None:
        """Handle to."""
        self.page = min(max(page, 0), self.max_page)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.render(), view=self)

    @discord.ui.button(label="« First", style=discord.ButtonStyle.secondary)
    async def first_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        """Handle button."""
        await self._go_to(0, interaction)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def previous_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        """Handle button."""
        await self._go_to(self.page - 1, interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        """Handle button."""
        await self._go_to(self.page + 1, interaction)

    @discord.ui.button(label="Last »", style=discord.ButtonStyle.secondary)
    async def last_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        """Handle button."""
        await self._go_to(self.max_page, interaction)
