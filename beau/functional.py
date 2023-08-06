import random
import discord

from discord.ext import commands, tasks
import time

start_time = time.time()


class Functional(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.up_time.start()

    @tasks.loop(seconds=1.0)
    async def up_time(self):
        await self.bot.change_presence(
            activity=discord.Game(
                name=f"Uptime: {(time.time() - start_time)/3600:.2f}/24 hours"
            )
        )

    @up_time.before_loop
    async def before_up_time(self):
        await self.bot.wait_until_ready()

    @commands.command()
    async def uptime(self, ctx):
        """
        Returns the uptime of the bot
        """
        await ctx.send(
            f"```Uptime: {(time.time() - start_time)/3600:.2f}/24 hours```"
        )

    @commands.command()
    async def ping(self, ctx):
        """
        Pong!
        """
        await ctx.send("```Pong!```")

    @commands.command()
    async def echo(self, ctx, *, content: str):
        """
        Echo!
        """
        await ctx.send("```" + content + "```")

    @commands.command()
    async def choice(self, ctx, *choices: str):
        """
        Choose between multiple choices. Example: !choice a, b, c
        """
        choices = [c.strip() for c in " ".join(choices).split(",")]
        await ctx.send("```" + random.choice(choices) + "```")
