import sqlite3
from bs4 import BeautifulSoup
import requests
import discord
from discord.ext import commands
import random
from youtubesearchpython import VideosSearch
import asyncio
import itertools
import sys
import traceback
from async_timeout import timeout
from functools import partial
import youtube_dl
from youtube_dl import YoutubeDL
import lyricsgenius
from db import BeauPlDb


def make_embed(text, title=""):
    return discord.Embed(title=title,
                         description=text,
                         color=discord.Color.from_rgb(255, 165, 158))


#
# Suppress noise about console usage from errors
youtube_dl.utils.bug_reports_message = lambda: ''

#####################
maintain_mode = True
#####################

db = BeauPlDb()

genius = lyricsgenius.Genius(
    'nyUuLcrHR6mi-g1L7vifIvNNaSoo_TOsHTVhPdCA63anhAuICQGcHPHHOaedq5jQ')

ytdlopts = {
    'format': 'bestaudio/best',
    'outtmpl': 'downloads/%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'  # ipv6 addresses cause issues sometimes
}

ffmpegopts = {
    'before_options': '-nostdin',
    'options': '-vn'
}

ytdl = YoutubeDL(ytdlopts)


class VoiceConnectionError(commands.CommandError):
    """Custom Exception class for connection errors."""


class InvalidVoiceChannel(VoiceConnectionError):
    """Exception for cases of invalid Voice Channels."""


class YTDLSource(discord.PCMVolumeTransformer):

    def __init__(self, source, *, data, requester):
        super().__init__(source)
        self.requester = requester

        self.title = data.get('title')
        self.web_url = data.get('webpage_url')
        self.duration = data.get('duration')

        # YTDL info dicts (data) have other useful information you might want
        # https://github.com/rg3/youtube-dl/blob/master/README.md

    def __getitem__(self, item: str):
        """Allows us to access attributes similar to a dict.
        This is only useful when you are NOT downloading.
        """
        return self.__getattribute__(item)

    @classmethod
    async def create_source(cls, ctx, search: str, *, loop, download=False):
        loop = loop or asyncio.get_event_loop()

        to_run = partial(ytdl.extract_info, url=search, download=download)
        data = await loop.run_in_executor(None, to_run)

        if 'entries' in data:
            # take first item from a playlist
            data = data['entries'][0]

        embed = make_embed(
            f"Đã thêm [{data['title']}]({data['webpage_url']}) [{ctx.author.mention}]")
        await ctx.send(embed=embed)

        if download:
            source = ytdl.prepare_filename(data)
        else:
            return {'webpage_url': data['webpage_url'], 'requester': ctx.author, 'title': data['title']}

        return cls(discord.FFmpegPCMAudio(source), data=data, requester=ctx.author)

    @classmethod
    async def regather_stream(cls, data, *, loop):
        """Used for preparing a stream, instead of downloading.
        Since Youtube Streaming links expire."""
        loop = loop or asyncio.get_event_loop()
        requester = data['requester']

        to_run = partial(ytdl.extract_info,
                         url=data['webpage_url'], download=False)
        data = await loop.run_in_executor(None, to_run)

        return cls(discord.FFmpegPCMAudio(data['url']), data=data, requester=requester)


class MusicPlayer:
    """A class which is assigned to each guild using the bot for Music.
    This class implements a queue and loop, which allows for different guilds to listen to different playlists
    simultaneously.
    When the bot disconnects from the Voice it's instance will be destroyed.
    """

    __slots__ = ('bot', '_guild', '_channel', '_cog',
                 'queue', 'next', 'current', 'np', 'volume', 'clone', 'isloop')

    def __init__(self, ctx):
        self.bot = ctx.bot
        self._guild = ctx.guild
        self._channel = ctx.channel
        self._cog = ctx.cog

        self.queue = asyncio.Queue()
        self.next = asyncio.Event()

        self.np = None  # Now playing message
        self.volume = .5
        self.current = None
        self.clone = None
        self.isloop = False

        ctx.bot.loop.create_task(self.player_loop())

    async def player_loop(self):
        """Our main player loop."""
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.next.clear()

            try:
                # Wait for the next song. If we timeout cancel the player and disconnect...
                if self.isloop and self.clone:
                    source = self.clone
                else:
                    async with timeout(600):  # 10 minutes...
                        source = await self.queue.get()
                        self.clone = source
            except asyncio.TimeoutError:
                return self.destroy(self._guild)

            if not isinstance(source, YTDLSource):
                # Source was probably a stream (not downloaded)
                # So we should regather to prevent stream expiration
                try:
                    source = await YTDLSource.regather_stream(source, loop=self.bot.loop)
                except Exception as e:
                    await self._channel.send(f'Bài này nó bị cái gì rồi á!\n'
                                             f'```css\n[{e}]\n```')
                    continue

            source.volume = self.volume
            self.current = source

            self._guild.voice_client.play(
                source, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))

            self.np = await self._channel.send(embed=make_embed(f"[{source.title}]({source.web_url}) [{source.requester.mention}]", title="Đang phát"))
            await self.next.wait()

            # Make sure the FFmpeg process is cleaned up.
            source.cleanup()
            self.current = None

    def destroy(self, guild):
        """Disconnect and cleanup the player."""
        return self.bot.loop.create_task(self._cog.cleanup(guild))


class Music(commands.Cog):
    """Music related commands."""

    __slots__ = ('bot', 'players')

    def __init__(self, bot):
        self.bot = bot
        self.players = {}

    async def cleanup(self, guild):
        try:
            await guild.voice_client.disconnect()
        except AttributeError:
            pass

        try:
            del self.players[guild.id]
        except KeyError:
            pass

    async def __local_check(self, ctx):
        """A local check which applies to all commands in this cog."""
        if not ctx.guild:
            raise commands.NoPrivateMessage
        return True

    async def __error(self, ctx, error):
        """A local error handler for all errors arising from commands in this cog."""
        if isinstance(error, commands.NoPrivateMessage):
            try:
                # return await ctx.send('This command can not be used in Private Messages.')
                return await ctx.send('Lỗi gì rồi (◕‿◕)')
            except discord.HTTPException:
                pass
        elif isinstance(error, InvalidVoiceChannel):
            # await ctx.send('Error connecting to Voice Channel. '
            #                'Please make sure you are in a valid channel or provide me with one')
            return await ctx.send('Lỗi gì rồi (◕‿◕)')

        print('Ignoring exception in command {}:'.format(
            ctx.command), file=sys.stderr)
        traceback.print_exception(
            type(error), error, error.__traceback__, file=sys.stderr)

    def get_player(self, ctx):
        """Retrieve the guild player, or generate one."""
        try:
            player = self.players[ctx.guild.id]
        except KeyError:
            player = MusicPlayer(ctx)
            self.players[ctx.guild.id] = player

        return player

    @commands.command(name='join', aliases=['connect', 'j', 'go', 'zo', 'vô'], description="Kết nối voice")
    async def connect_(self, ctx, *, channel: discord.VoiceChannel = None):
        """Kết nối voice.
        Parameters
        ------------
        Beau sẽ vô voice channel hiện tại của bạn.
        """
        if not channel:
            try:
                channel = ctx.author.voice.channel
            except AttributeError:
                await ctx.send(embed=make_embed("Rồi vô đâu cơ?"))
                raise InvalidVoiceChannel(
                    'Rồi vô đâu cơ? Chẳng biết vô đâu')

        vc = ctx.voice_client

        if vc:
            if vc.channel.id == channel.id:
                return
            try:
                await vc.move_to(channel)
            except asyncio.TimeoutError:
                raise VoiceConnectionError(
                    f'<{channel}> timed out rùi neeee.')
        else:
            try:
                await channel.connect()
            except asyncio.TimeoutError:
                raise VoiceConnectionError(
                    f'<{channel}> timed out luôngggg.')
        await ctx.send(embed=make_embed(f"Vô {channel} rồi nhá! ( づ￣ ³￣ )づ"))

    @commands.command(name='play', aliases=['sing', 'p', 'phát', 'hát'], description="stream nhạc từ youtube")
    async def play_(self, ctx, *, search: str):
        """Thêm một bài hát vào hàng đợi.
        Parameters
        ------------
        search: str
            Tên bài hát, ID, hoặc URL
        """
        await ctx.trigger_typing()

        vc = ctx.voice_client

        if not vc:
            await ctx.invoke(self.connect_)

        player = self.get_player(ctx)

        # If download is False, source will be a dict which will be used later to regather the stream.
        # If download is True, source will be a discord.FFmpegPCMAudio with a VolumeTransformer.
        source = await YTDLSource.create_source(ctx, search, loop=self.bot.loop, download=False)

        await player.queue.put(source)

    @commands.command(name='loop', aliases=['lặp', 'again', 'repeat'], description="lặp bài hát hiện tại")
    async def loop_(self, ctx):
        player = self.get_player(ctx)
        embed = None
        if player.isloop:
            player.isloop = False
            embed = make_embed("Hết lặp rùi (⌐■_■)")
        else:
            player.isloop = True
            embed = make_embed("Oki để lặp (⌐■_■)")
        await ctx.send(embed=embed)

    @commands.command(name='pause', aliases=['dừng'], description="dừng nhạc")
    async def pause_(self, ctx):
        """Tạm dừng bài hát đang phát."""
        vc = ctx.voice_client

        if not vc or not vc.is_playing():
            embed = make_embed("Có phát bài nào đâu mà đòi dừng? (｡･･｡)")
            return await ctx.send(embed=embed)
        elif vc.is_paused():
            return

        vc.pause()
        embed = make_embed("Dừng thì dừng (｡･･｡)")
        await ctx.send(embed=embed)

    @commands.command(name='resume', aliases=['tiếp'], description="tiếp tục phát")
    async def resume_(self, ctx):
        """Tiếp tục phát bài hát đã dừng."""
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="Rồi ai nghe?",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)
        elif not vc.is_paused():
            return

        vc.resume()
        embed = discord.Embed(title="", description="Tiếp tiếp tiếp ( ✿◠‿◠ )",
                              color=discord.Color.from_rgb(255, 165, 158))
        await ctx.send(embed=embed)

    @commands.command(name='skip', description="bỏ qua bài hiện tại")
    async def skip_(self, ctx):
        """Bỏ qua bài hát đang phát."""
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="Skip cái gì?",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        if vc.is_paused():
            pass
        elif not vc.is_playing():
            return

        vc.stop()

    @commands.command(name='remove', aliases=['rm', 'rem', 'bỏ số', 'bỏ bài số'],
                      description="loại bỏ bài hất")
    async def remove_(self, ctx, pos: int = None):
        """Bỏ bài hát khỏi hàng chờ"""

        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="Rồi xoá cái gì?",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        player = self.get_player(ctx)
        if pos == None:
            try:
                player.queue._queue.pop()
            except IndexError:
                embed = discord.Embed(title="", description=f'Làm gì còn bài nào (⁄ ⁄•⁄ω⁄•⁄ ⁄)⁄',
                                      color=discord.Color.from_rgb(255, 165, 158))
                await ctx.send(embed=embed)
        else:
            try:
                s = player.queue._queue[pos - 1]
                del player.queue._queue[pos - 1]
                embed = discord.Embed(title="",
                                      description=f"Đã xoá [{s['title']}]({s['webpage_url']}) [{s['requester'].mention}] ra khỏi hàng chờ ฅ^•ﻌ•^ฅ...",
                                      color=discord.Color.from_rgb(255, 165, 158))
                await ctx.send(embed=embed)
            except:
                embed = discord.Embed(title="", description=f'Làm gì có bài nào số {pos} (⁄ ⁄•⁄ω⁄•⁄ ⁄)⁄',
                                      color=discord.Color.from_rgb(255, 165, 158))
                await ctx.send(embed=embed)

    @commands.command(name='move', aliases=['swap'],
                      description="Đổi vị trí")
    async def move_(self, ctx, src: int = None, des: int = None):
        """Đổi vị trí bài hát"""

        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="Rồi xoá cái gì?",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        player = self.get_player(ctx)
        try:
            s = player.queue._queue[src - 1]
            player.queue._queue[src - 1] = player.queue._queue[des - 1]
            player.queue._queue[des - 1] = s
            embed = discord.Embed(title="",
                                  description=f"Đã thay đổi vị trí bài {src} với bài {des}＼(ﾟｰﾟ＼)",
                                  color=discord.Color.from_rgb(255, 165, 158))
            await ctx.send(embed=embed)
        except:
            embed = discord.Embed(title="", description=f'Coi lại số thứ tự đi bạn (⁄ ⁄•⁄ω⁄•⁄ ⁄)⁄',
                                  color=discord.Color.from_rgb(255, 165, 158))
            await ctx.send(embed=embed)

    @commands.command(name='clear', aliases=['clr', 'cl', 'cr'], description="dọn dẹp hàng chờ")
    async def clear_(self, ctx):
        """Xoá hết toàn bộ hàng chờ."""

        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="Dùng lệnh join đi nào! ( つ´∀｀)つ ",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        player = self.get_player(ctx)
        player.queue._queue.clear()
        embed = discord.Embed(title="", description="Dọn hàng chờ sạch sẽ ròi nhá ( >ω<)",
                              color=discord.Color.from_rgb(255, 165, 158))
        await ctx.send(embed=embed)

    @commands.command(name='queue', aliases=['q', 'que'], description="xem hàng chờ")
    async def queue_info(self, ctx):
        """Xem hàng đợi hiện tại."""
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="Dùng lệnh join đi nào! ( つ´∀｀)つ ",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        player = self.get_player(ctx)
        if player.queue.empty():
            embed = discord.Embed(title="", description="Trống lơ trống lốc",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        seconds = vc.source.duration % (24 * 3600)
        hour = seconds // 3600
        seconds %= 3600
        minutes = seconds // 60
        seconds %= 60
        if hour > 0:
            duration = "%dh %02dm %02ds" % (hour, minutes, seconds)
        else:
            duration = "%02dm %02ds" % (minutes, seconds)

        # Grabs the songs in the queue...
        upcoming = list(itertools.islice(player.queue._queue,
                        0, int(len(player.queue._queue))))
        fmt = '\n'.join(
            f"`{(upcoming.index(_)) + 1}.` [{_['title']}]({_['webpage_url']}) | `Thêm bởi: {_['requester']}`\n"
            for _ in upcoming)
        fmt = f"\n__Đang phát__:\n[{vc.source.title}]({vc.source.web_url}) | `{duration}` `Thêm bởi: {vc.source.requester}`\n\n__Tiếp theo:__\n" + \
            fmt + f"\n**{len(upcoming)} bài nữa trong hàng chờ**"
        if player.isloop:
            embed = discord.Embed(title=f'{ctx.guild.name} (Loop enable)', description=fmt,
                                  color=discord.Color.from_rgb(255, 165, 158))
        else:
            embed = discord.Embed(title=f'{ctx.guild.name}', description=fmt,
                                  color=discord.Color.from_rgb(255, 165, 158))
        embed.set_footer(text=f"{ctx.author.display_name}",
                         icon_url=ctx.author.avatar_url)

        await ctx.send(embed=embed)

    @commands.command(name='np', aliases=['song', 'current', 'currentsong', 'playing'],
                      description="bài hát đang phát")
    async def now_playing_(self, ctx):
        """Xem thông tin bài hát đang phát."""
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="Dùng lệnh join đi nào! ( つ´∀｀)つ ",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        player = self.get_player(ctx)
        if not player.current:
            embed = discord.Embed(title="", description="Không có bài nào hết á ˖◛⁺ ⑅ ♡",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        seconds = vc.source.duration % (24 * 3600)
        hour = seconds // 3600
        seconds %= 3600
        minutes = seconds // 60
        seconds %= 60
        if hour > 0:
            duration = "%dh %02dm %02ds" % (hour, minutes, seconds)
        else:
            duration = "%02dm %02ds" % (minutes, seconds)

        embed = discord.Embed(title="",
                              description=f"[{vc.source.title}]({vc.source.web_url}) | `{duration}` `Thêm bởi: {vc.source.requester.name}`",
                              color=discord.Color.from_rgb(255, 165, 158))
        embed.set_author(icon_url=self.bot.user.avatar_url, name=f"Đang phát")
        await ctx.send(embed=embed)

    @commands.command(name='volume', aliases=['vol', 'v'], description="thay đổi âm lượng")
    async def change_volume(self, ctx, *, vol: float = None):
        """Thay đổi âm lượng của Beau.
        Parameters
        ------------
        volume: float or int
            Phần trăm âm lượng
        """
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="Dùng lệnh join đi nào! ( つ´∀｀)つ ",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        if not vol:
            embed = discord.Embed(title="", description=f"🔊 {(vc.source.volume) * 100}%",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        if not 0 < vol < 101:
            embed = discord.Embed(title="", description="Nhập vào từ 1 đến 100 nhé!",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        player = self.get_player(ctx)
        if vc.source:
            vc.source.volume = vol / 100

        player.volume = vol / 100
        embed = discord.Embed(title="", description=f'Đã chỉnh âm lượng về {vol}%',
                              color=discord.Color.from_rgb(255, 165, 158))
        await ctx.send(embed=embed)

    @commands.command(name='leave', aliases=["stop", "dc", "disconnect", "bye", 'ra', 'đi'],
                      description="ngừng phát nhạc và ngắt kết nối room")
    async def leave_(self, ctx):
        """Dừng phát và huỷ hàng chờ."""
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="Dùng lệnh join đi nào! ( つ´∀｀)つ ",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        embed = discord.Embed(title="", description="Đi ngủ đây (⊃◜⌓◝⊂)",
                              color=discord.Color.from_rgb(255, 165, 158))
        await ctx.send(embed=embed)
        await self.cleanup(ctx.guild)

    @commands.command(name='lyrics', description="lời bài hát")
    async def lyrics_(self, ctx, *, search=None):
        """Tìm lời bài hát."""
        if search != None:
            song = genius.search_song(title=search)
            embed = discord.Embed(title=song.title, description=song.lyrics,
                                  color=discord.Color.from_rgb(255, 165, 158))
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(description='Nhập vô tên bài hát đi nào! ( つ´∀｀)つ ',
                                  color=discord.Color.from_rgb(255, 165, 158))
            await ctx.send(embed=embed)

    @commands.command(name='search', description="tìm bài hát trên youtube")
    async def search_(self, ctx, *, query=None):
        """Tìm kiếm trên Youtube."""
        if query != None:

            videosSearch = VideosSearch(query, limit=5).result()['result']

            fmt = '\n'.join(
                f"`{(videosSearch.index(_)) + 1}.` `{_['title'].replace('|', '-')}` | `{_['duration']}`"
                for _ in videosSearch)
            embed = discord.Embed(title=f'Kết quả tìm kiếm cho "{query}"', description=fmt,
                                  color=discord.Color.from_rgb(255, 165, 158))
            embed.set_footer(
                text=f"Nhập vào lựa chọn của bạn (1-{len(videosSearch)} | 0 để huỷ):")

            await ctx.send(embed=embed)

            def check(m):
                try:
                    if 0 <= int(m.content) <= len(videosSearch):
                        return True
                    else:
                        return False
                except:
                    return False

            msg = await bot.wait_for("message", check=check, timeout=10)

            if msg.content != '0':

                await ctx.trigger_typing()

                vc = ctx.voice_client

                if not vc:
                    await ctx.invoke(self.connect_)

                player = self.get_player(ctx)

                source = await YTDLSource.create_source(ctx, videosSearch[int(msg.content)-1]['id'], loop=self.bot.loop, download=False)

                await player.queue.put(source)

            else:

                embed = discord.Embed(title="", description="Đã huỷ tìm kiếm (⊃◜⌓◝⊂)",
                                      color=discord.Color.from_rgb(255, 165, 158))
                await ctx.send(embed=embed)

        else:
            embed = discord.Embed(description='Nhập vô tên bài hát đi nào! ( つ´∀｀)つ ',
                                  color=discord.Color.from_rgb(255, 165, 158))
            await ctx.send(embed=embed)


class Image(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def cat(self, ctx):
        """Beau sẽ gửi cho bạn ảnh mèo cute."""
        response = requests.get('https://aws.random.cat/meow')
        data = response.json()
        await ctx.send(data['file'])

    @commands.command()
    async def dog(self, ctx):
        """Beau sẽ gửi cho bạn ảnh chó khó ưa."""
        response = requests.get('https://dog.ceo/api/breeds/image/random')
        data = response.json()
        await ctx.send(data['message'])


class Game(commands.Cog):
    @commands.command(name='tft', description="lối lên trang bị ĐTCL")
    async def tft_(self, ctx, champion: str):
        """Tra cứu lối lên trang bị ĐTCL."""
        url = 'https://app.mobalytics.gg/tft/champions/' + \
            champion.replace(' ', '-')

        page = requests.get(url)

        soup = BeautifulSoup(page.content, 'html.parser')

        results = soup.find_all('div', class_='m-lff1ls elh7uow9')
        if len(results) != 0:
            for item in results:
                embed = discord.Embed(title=item.find(
                    'p', class_='m-zow6u1 elh7uow4').text)
                embed.set_thumbnail(url=item.find('img').get('src'))
                await ctx.send(embed=embed)
        else:
            await ctx.send(f'Có cc mà {champion}')


class Playlist(commands.Cog):
    @commands.command(name='playlist', aliases=['pl'], description="Thao tác Playlist")
    async def playlist(self, ctx):
        """Thao tác Playlist."""
        pls = db.get_playlist(ctx.guild.id)
        if pls:
            fmt = '\n'.join(
                f"`pid:{(_[0])}` `{_[1]}` | được `{_[-1]}` tạo ngày `{_[3]}`"
                for _ in pls)
            await ctx.send(embed=make_embed(fmt, title=f'Playlist đã tạo của {ctx.guild.name}'))

        else:
            await ctx.send(embed=make_embed('Ko có mẹ gì hết'))

    @commands.command(name='playlist_add', aliases=['pladd'], description="Tạo playlist mới")
    async def playlist_add(self, ctx, *, pname):
        """Thêm playlist."""
        status = db.add_playlist(pname, ctx.guild.id, ctx.author.name)
        if status:
            await ctx.send(embed=make_embed(f'Tạo "{pname}" rùi nhá'))
        else:
            await ctx.send(embed=make_embed('Lỗi mẹ nó rồi nhá'))

    @commands.command(name='playlist_rm', aliases=['plrm'], description="Xoá playlist")
    async def playlist_rm(self, ctx, *, pid):
        """Xoá playlist."""
        status = db.rm_playlist(pid)
        if status:
            await ctx.send(embed=make_embed(f'Đã xoá pid = {pid}"'))
        else:
            await ctx.send(embed=make_embed('Lỗi mẹ nó rồi nhá'))

#############################################################################################


def setup(bot):
    bot.add_cog(Image(bot))
    bot.add_cog(Music(bot))
    bot.add_cog(Playlist(bot))
    bot.add_cog(Game(bot))


bot = commands.Bot(command_prefix=commands.when_mentioned_or("!"),
                   description='Các lệnh cơ bản')


@bot.event
async def on_ready():
    if maintain_mode:
        await bot.change_presence(status=discord.Status.do_not_disturb, activity=discord.Activity(type=discord.ActivityType.listening, name="Đang nâng cấp :("))
    else:
        await bot.change_presence(status=discord.Status.online, activity=discord.Activity(type=discord.ActivityType.watching, name="Batman: Vengeance"))
    print("Bot is ready!")


setup(bot)
bot.run('NjgzNjQ2MzE4MzE2NjE3NzU4.Gv7ha9.6RPmbxoISolSaFhyyv_mUW4EGqtet3vWLhFew8')
