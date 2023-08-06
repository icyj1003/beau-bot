import asyncio
import os

import discord
from discord.ext import commands

from functional import Functional
from music import Music

TOKEN = os.environ["DISCORD_TOKEN"]


async def setup(bot):
    async with bot:
        await bot.add_cog(Music(bot))
        await bot.add_cog(Functional(bot))
        await bot.start(TOKEN)


bot = commands.Bot(
    command_prefix=commands.when_mentioned_or("!"),
    description="Basic usage of Beau",
    intents=discord.Intents.all(),
)


@bot.event
async def on_ready():
    print("Logged in as {0} ({0.id})".format(bot.user))
    print("------")
    await bot.change_presence(activity=discord.Game(name="!help"))


asyncio.run(setup(bot))
