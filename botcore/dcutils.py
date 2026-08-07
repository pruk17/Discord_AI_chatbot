"""Small Discord message helpers shared across modules."""

import discord

from . import config


def split_message(text: str) -> list[str]:
    """Split a long reply into <=2000 char chunks Discord will accept."""
    limit = config.DISCORD_MESSAGE_LIMIT
    return [text[i : i + limit] for i in range(0, len(text), limit)] or ["(empty)"]


async def send_interaction(interaction: discord.Interaction, text: str, ephemeral: bool = False):
    """Reply to a slash command, splitting long text across follow-ups."""
    chunks = split_message(text)
    await interaction.response.send_message(chunks[0], ephemeral=ephemeral)
    for chunk in chunks[1:]:
        await interaction.followup.send(chunk, ephemeral=ephemeral)
