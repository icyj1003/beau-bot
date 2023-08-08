import asyncio
import json

import discord
import yt_dlp as youtube_dl
from discord.ext import commands
from tabulate import tabulate
from youtube_search import YoutubeSearch
from repo import BeauRepo
import os


def song_from_query(query, max_results=1):
    if query.startswith("https://www.youtube.com/watch?v="):
        query = query.split("v=")[1]

    rs = YoutubeSearch(query, max_results=max_results).to_dict()

    return rs


# Suppress noise about console usage from errors
youtube_dl.utils.bug_reports_message = lambda: ""
json_serializer = lambda x: json.dumps(x).encode("utf8")
json_deserializer = lambda x: json.loads(x.decode("utf8"))


ytdl_format_options = {
    "format": "bestaudio/best",
    "outtmpl": "%(extractor)s-%(id)s-%(title)s.%(ext)s",
    "restrictfilenames": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "auto",
    "source_address": "0.0.0.0",
}

ffmpeg_options = {
    "options": "-vn",
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)

        self.data = data
        self.title = data.get("title")
        self.url = data.get("url")

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(
            None, lambda: ytdl.extract_info(url, download=not stream)
        )

        if "entries" in data:
            data = data["entries"][0]

        filename = data["url"] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.np_song = None
        self.repo = BeauRepo()

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            try:
                self.repo.init(guild.id, guild.name)
            except Exception as e:
                print(e)

    async def add_to_queue(self, ctx, song):
        try:
            self.repo.add_song(ctx.guild.id, song)
            await ctx.send("```Added {} to queue```".format(song["title"]))
        except Exception as e:
            await ctx.send(
                "```Failed to add {} to queue. {}```".format(song["title"], e)
            )
        if not ctx.voice_client.is_playing():
            await self.play_next(ctx)

    async def play_next(self, ctx, destroy=False):
        if self.repo.length(ctx.guild.id) > 0:
            song = self.repo.get_song(ctx.guild.id, 0)
            self.np_song = song
            url = "https://www.youtube.com" + song["url_suffix"]

            player = await YTDLSource.from_url(
                url,
                loop=self.bot.loop,
                stream=True,
            )

            ctx.voice_client.play(
                player,
                after=lambda e: self.bot.loop.create_task(self.play_next(ctx)),
            )

            await ctx.send("```Now playing: {}```".format(song["title"]))

            if not self.repo.is_loop(ctx.guild.id):
                self.repo.remove_song(ctx.guild.id, 0)

    @commands.command(aliases=["l"])
    async def loop(self, ctx):
        """
        Loops the current song
        """
        if ctx.voice_client:
            is_loop = self.repo.trigger_loop(ctx.guild.id)
            await ctx.send("```Looping is {}```".format("on" if is_loop else "off"))
        else:
            await ctx.send("```I'm not in a voice channel```")

    @commands.command(aliases=["v", "vol"])
    async def volume(self, ctx, volume: int):
        """
        Changes the player's volume
        """
        if ctx.voice_client:
            self.repo.set_volume(ctx.guild.id, volume)
            ctx.voice_client.source.volume = volume / 100
            await ctx.send("```Changed volume to {}%```".format(volume))
        else:
            await ctx.send("```I'm not in a voice channel```")

    @commands.command(aliases=["p"])
    async def play(self, ctx, *query):
        """
        Plays a song from youtube based on the query (or url) provided
        """
        if ctx.voice_client:
            song = song_from_query(" ".join(query))[0]
            await self.add_to_queue(ctx, song)
        else:
            await ctx.send("```I'm not in a voice channel```")

    @commands.command(aliases=["s"])
    async def skip(self, ctx):
        """
        Skips the current song
        """
        if ctx.voice_client:
            ctx.voice_client.stop()
            await ctx.send("```Skipped```")
            await self.play_next(ctx)
        else:
            await ctx.send("```I'm not in a voice channel```")

    @commands.command(name="queue", aliases=["q"])
    async def show_queue(self, ctx):
        """
        Shows the current queue
        """
        if ctx.voice_client:
            playlist = self.repo.get_playlist(ctx.guild.id)
            if len(playlist) > 0:
                queue_info = [
                    {
                        "-": i + 1,
                        "Title": q["title"]
                        if len(q["title"]) < 50
                        else q["title"][:50] + " ...",
                        "Duration": q["duration"],
                    }
                    for i, q in enumerate(playlist)
                ]

                table = "```Queue of {}\n\n{}```".format(
                    ctx.guild.name,
                    tabulate(
                        queue_info,
                        headers="keys",
                        tablefmt="simple_outline",
                        colalign=("center", "left", "right"),
                    ),
                )
                await ctx.send(table)

            else:
                await ctx.send("```Queue is empty```")

    @commands.command(alises=["r"])
    async def remove(self, ctx, index: int):
        """
        Removes a song from the queue
        """
        if ctx.voice_client:
            try:
                self.repo.remove_song(ctx.guild.id, index - 1)
                await ctx.send("```Removed song at index {}```".format(index))
                await self.show_queue(ctx)
            except Exception as e:
                await ctx.send("```Failed to remove song. {}```".format(e))
        else:
            await ctx.send("```I'm not in a voice channel```")

    @commands.command()
    async def up(self, ctx, index: int):
        """
        Moves a song up in the queue
        """
        if ctx.voice_client:
            playlist = self.repo.get_playlist(ctx.guild.id)
            if len(playlist) > 0:
                if index > 1 and index <= len(playlist):
                    song = self.repo.get_song(ctx.guild.id, index - 1)
                    self.repo.move_song_up(ctx.guild.id, index - 1)
                    await ctx.send("```Moved {} up```".format(song["title"]))
                    await self.show_queue(ctx)

                else:
                    await ctx.send("```Invalid index```")
            else:
                await ctx.send("```Queue is empty```")
        else:
            await ctx.send("```I'm not in a voice channel```")

    @commands.command()
    async def swap(self, ctx, index1: int, index2: int):
        """
        Swaps two songs in the queue
        """
        if ctx.voice_client:
            playlist = self.repo.get_playlist(ctx.guild.id)
            if len(playlist) > 0:
                song1 = self.repo.get_song(ctx.guild.id, index1 - 1)
                song2 = self.repo.get_song(ctx.guild.id, index1 - 1)
                self.repo.swap_songs(ctx.guild.id, index1 - 1, index2 - 1)
                await ctx.send(
                    "```Swapped {} and {}```".format(song1["title"], song2["title"])
                )
                await self.show_queue(ctx)
            else:
                await ctx.send("```Queue is empty```")
        else:
            await ctx.send("```I'm not in a voice channel```")

    @commands.command()
    async def clear(self, ctx):
        """
        Clears the queue
        """
        if ctx.voice_client:
            self.repo.clear_playlist(ctx.guild.id)
            await ctx.send("```Queue cleared```")
        else:
            await ctx.send("```I'm not in a voice channel```")

    @commands.command()
    async def search(self, ctx, *query):
        """
        Searches youtube for a song
        """
        rs = song_from_query(" ".join(query), max_results=10)
        rs_info = [
            {
                "-": i + 1,
                "Title": q["title"]
                if len(q["title"]) < 50
                else q["title"][:50] + " ...",
                "Duration": q["duration"],
            }
            for i, q in enumerate(rs)
        ]

        await ctx.send(
            "```Result for '{}':\n\n{}\n\nEnter the number of the song you want to play:```".format(
                " ".join(query),
                tabulate(
                    rs_info,
                    headers="keys",
                    tablefmt="simple_outline",
                    colalign=("center", "left", "right"),
                ),
            )
        )

        def check(m):
            try:
                if (
                    m.author == ctx.author
                    and m.channel == ctx.channel
                    and 0 <= int(m.content) <= len(rs)
                ):
                    return True
                else:
                    return False
            except:
                return False

        try:
            msg = await self.bot.wait_for("message", check=check, timeout=30)
            await self.add_to_queue(ctx, rs[int(msg.content) - 1])

        except asyncio.TimeoutError:
            await ctx.send("```Timeout```")

    @commands.command()
    async def pause(self, ctx):
        """
        Pauses the current song
        """
        if ctx.voice_client:
            ctx.voice_client.pause()
            await ctx.send("```Paused```")
        else:
            await ctx.send("```I'm not in a voice channel```")

    @commands.command()
    async def resume(self, ctx):
        """
        Resumes the current song
        """
        if ctx.voice_client:
            ctx.voice_client.resume()
            await ctx.send("```Resumed```")
        else:
            await ctx.send("```I'm not in a voice channel```")

    @commands.command(alises=["j"])
    async def join(self, ctx):
        """
        Joins the voice channel you are in
        """
        if ctx.author.voice:
            if not ctx.voice_client:
                await ctx.author.voice.channel.connect()
            else:
                await ctx.voice_client.move_to(ctx.author.voice.channel)
        else:
            await ctx.send("```You are not in a voice channel```")

    @commands.command(alises=["np"])
    async def now_playing(self, ctx):
        """
        Shows the current song
        """
        if ctx.voice_client:
            if self.np_song:
                await ctx.send("```Now playing: {}```".format(self.np_song["title"]))
            else:
                await ctx.send("```Nothing is playing```")
        else:
            await ctx.send("```I'm not in a voice channel```")

    @commands.command()
    async def leave(self, ctx):
        """
        Leaves the voice channel
        """
        if ctx.voice_client:
            self.repo.clear_playlist(ctx.guild.id)
            await ctx.voice_client.disconnect()
        else:
            await ctx.send("```I'm not in a voice channel```")
