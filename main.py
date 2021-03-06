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
            f"???? th??m [{data['title']}]({data['webpage_url']}) [{ctx.author.mention}]")
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
                    await self._channel.send(f'B??i n??y n?? b??? c??i g?? r???i ??!\n'
                                             f'```css\n[{e}]\n```')
                    continue

            source.volume = self.volume
            self.current = source

            self._guild.voice_client.play(
                source, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))

            self.np = await self._channel.send(embed=make_embed(f"[{source.title}]({source.web_url}) [{source.requester.mention}]", title="??ang ph??t"))
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
                return await ctx.send('L???i g?? r???i (?????????)')
            except discord.HTTPException:
                pass
        elif isinstance(error, InvalidVoiceChannel):
            # await ctx.send('Error connecting to Voice Channel. '
            #                'Please make sure you are in a valid channel or provide me with one')
            return await ctx.send('L???i g?? r???i (?????????)')

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

    @commands.command(name='join', aliases=['connect', 'j', 'go', 'zo', 'v??'], description="K???t n???i voice")
    async def connect_(self, ctx, *, channel: discord.VoiceChannel = None):
        """K???t n???i voice.
        Parameters
        ------------
        Beau s??? v?? voice channel hi???n t???i c???a b???n.
        """
        if not channel:
            try:
                channel = ctx.author.voice.channel
            except AttributeError:
                await ctx.send(embed=make_embed("R???i v?? ????u c???"))
                raise InvalidVoiceChannel(
                    'R???i v?? ????u c??? Ch???ng bi???t v?? ????u')

        vc = ctx.voice_client

        if vc:
            if vc.channel.id == channel.id:
                return
            try:
                await vc.move_to(channel)
            except asyncio.TimeoutError:
                raise VoiceConnectionError(
                    f'<{channel}> timed out r??i neeee.')
        else:
            try:
                await channel.connect()
            except asyncio.TimeoutError:
                raise VoiceConnectionError(
                    f'<{channel}> timed out lu??ngggg.')
        await ctx.send(embed=make_embed(f"V?? {channel} r???i nh??! ( ?????? ????? )???"))

    @commands.command(name='play', aliases=['sing', 'p', 'ph??t', 'h??t'], description="stream nh???c t??? youtube")
    async def play_(self, ctx, *, search: str):
        """Th??m m???t b??i h??t v??o h??ng ?????i.
        Parameters
        ------------
        search: str
            T??n b??i h??t, ID, ho???c URL
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

    @commands.command(name='loop', aliases=['l???p', 'again', 'repeat'], description="l???p b??i h??t hi???n t???i")
    async def loop_(self, ctx):
        player = self.get_player(ctx)
        embed = None
        if player.isloop:
            player.isloop = False
            embed = make_embed("H???t l???p r??i (??????_???)")
        else:
            player.isloop = True
            embed = make_embed("Oki ????? l???p (??????_???)")
        await ctx.send(embed=embed)

    @commands.command(name='pause', aliases=['d???ng'], description="d???ng nh???c")
    async def pause_(self, ctx):
        """T???m d???ng b??i h??t ??ang ph??t."""
        vc = ctx.voice_client

        if not vc or not vc.is_playing():
            embed = make_embed("C?? ph??t b??i n??o ????u m?? ????i d???ng? (????????????)")
            return await ctx.send(embed=embed)
        elif vc.is_paused():
            return

        vc.pause()
        embed = make_embed("D???ng th?? d???ng (????????????)")
        await ctx.send(embed=embed)

    @commands.command(name='resume', aliases=['ti???p'], description="ti???p t???c ph??t")
    async def resume_(self, ctx):
        """Ti???p t???c ph??t b??i h??t ???? d???ng."""
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="R???i ai nghe?",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)
        elif not vc.is_paused():
            return

        vc.resume()
        embed = discord.Embed(title="", description="Ti???p ti???p ti???p ( ???????????? )",
                              color=discord.Color.from_rgb(255, 165, 158))
        await ctx.send(embed=embed)

    @commands.command(name='skip', description="b??? qua b??i hi???n t???i")
    async def skip_(self, ctx):
        """B??? qua b??i h??t ??ang ph??t."""
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="Skip c??i g???",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        if vc.is_paused():
            pass
        elif not vc.is_playing():
            return

        vc.stop()

    @commands.command(name='remove', aliases=['rm', 'rem', 'b??? s???', 'b??? b??i s???'],
                      description="lo???i b??? b??i h???t")
    async def remove_(self, ctx, pos: int = None):
        """B??? b??i h??t kh???i h??ng ch???"""

        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="R???i xo?? c??i g???",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        player = self.get_player(ctx)
        if pos == None:
            try:
                player.queue._queue.pop()
            except IndexError:
                embed = discord.Embed(title="", description=f'L??m g?? c??n b??i n??o (??? ???????????????????? ???)???',
                                      color=discord.Color.from_rgb(255, 165, 158))
                await ctx.send(embed=embed)
        else:
            try:
                s = player.queue._queue[pos - 1]
                del player.queue._queue[pos - 1]
                embed = discord.Embed(title="",
                                      description=f"???? xo?? [{s['title']}]({s['webpage_url']}) [{s['requester'].mention}] ra kh???i h??ng ch??? ???^?????????^???...",
                                      color=discord.Color.from_rgb(255, 165, 158))
                await ctx.send(embed=embed)
            except:
                embed = discord.Embed(title="", description=f'L??m g?? c?? b??i n??o s??? {pos} (??? ???????????????????? ???)???',
                                      color=discord.Color.from_rgb(255, 165, 158))
                await ctx.send(embed=embed)

    @commands.command(name='move', aliases=['swap'],
                      description="?????i v??? tr??")
    async def move_(self, ctx, src: int = None, des: int = None):
        """?????i v??? tr?? b??i h??t"""

        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="R???i xo?? c??i g???",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        player = self.get_player(ctx)
        try:
            s = player.queue._queue[src - 1]
            player.queue._queue[src - 1] = player.queue._queue[des - 1]
            player.queue._queue[des - 1] = s
            embed = discord.Embed(title="",
                                  description=f"???? thay ?????i v??? tr?? b??i {src} v???i b??i {des}???(????????????)",
                                  color=discord.Color.from_rgb(255, 165, 158))
            await ctx.send(embed=embed)
        except:
            embed = discord.Embed(title="", description=f'Coi l???i s??? th??? t??? ??i b???n (??? ???????????????????? ???)???',
                                  color=discord.Color.from_rgb(255, 165, 158))
            await ctx.send(embed=embed)

    @commands.command(name='clear', aliases=['clr', 'cl', 'cr'], description="d???n d???p h??ng ch???")
    async def clear_(self, ctx):
        """Xo?? h???t to??n b??? h??ng ch???."""

        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="D??ng l???nh join ??i n??o! ( ???????????)??? ",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        player = self.get_player(ctx)
        player.queue._queue.clear()
        embed = discord.Embed(title="", description="D???n h??ng ch??? s???ch s??? r??i nh?? ( >??<)",
                              color=discord.Color.from_rgb(255, 165, 158))
        await ctx.send(embed=embed)

    @commands.command(name='queue', aliases=['q', 'que'], description="xem h??ng ch???")
    async def queue_info(self, ctx):
        """Xem h??ng ?????i hi???n t???i."""
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="D??ng l???nh join ??i n??o! ( ???????????)??? ",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        player = self.get_player(ctx)
        if player.queue.empty():
            embed = discord.Embed(title="", description="Tr???ng l?? tr???ng l???c",
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
            f"`{(upcoming.index(_)) + 1}.` [{_['title']}]({_['webpage_url']}) | `Th??m b???i: {_['requester']}`\n"
            for _ in upcoming)
        fmt = f"\n__??ang ph??t__:\n[{vc.source.title}]({vc.source.web_url}) | `{duration}` `Th??m b???i: {vc.source.requester}`\n\n__Ti???p theo:__\n" + \
            fmt + f"\n**{len(upcoming)} b??i n???a trong h??ng ch???**"
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
                      description="b??i h??t ??ang ph??t")
    async def now_playing_(self, ctx):
        """Xem th??ng tin b??i h??t ??ang ph??t."""
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="D??ng l???nh join ??i n??o! ( ???????????)??? ",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        player = self.get_player(ctx)
        if not player.current:
            embed = discord.Embed(title="", description="Kh??ng c?? b??i n??o h???t ?? ???????? ??? ???",
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
                              description=f"[{vc.source.title}]({vc.source.web_url}) | `{duration}` `Th??m b???i: {vc.source.requester.name}`",
                              color=discord.Color.from_rgb(255, 165, 158))
        embed.set_author(icon_url=self.bot.user.avatar_url, name=f"??ang ph??t")
        await ctx.send(embed=embed)

    @commands.command(name='volume', aliases=['vol', 'v'], description="thay ?????i ??m l?????ng")
    async def change_volume(self, ctx, *, vol: float = None):
        """Thay ?????i ??m l?????ng c???a Beau.
        Parameters
        ------------
        volume: float or int
            Ph???n tr??m ??m l?????ng
        """
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="D??ng l???nh join ??i n??o! ( ???????????)??? ",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        if not vol:
            embed = discord.Embed(title="", description=f"???? {(vc.source.volume) * 100}%",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        if not 0 < vol < 101:
            embed = discord.Embed(title="", description="Nh???p v??o t??? 1 ?????n 100 nh??!",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        player = self.get_player(ctx)
        if vc.source:
            vc.source.volume = vol / 100

        player.volume = vol / 100
        embed = discord.Embed(title="", description=f'???? ch???nh ??m l?????ng v??? {vol}%',
                              color=discord.Color.from_rgb(255, 165, 158))
        await ctx.send(embed=embed)

    @commands.command(name='leave', aliases=["stop", "dc", "disconnect", "bye", 'ra', '??i'],
                      description="ng???ng ph??t nh???c v?? ng???t k???t n???i room")
    async def leave_(self, ctx):
        """D???ng ph??t v?? hu??? h??ng ch???."""
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="D??ng l???nh join ??i n??o! ( ???????????)??? ",
                                  color=discord.Color.from_rgb(255, 165, 158))
            return await ctx.send(embed=embed)

        embed = discord.Embed(title="", description="??i ng??? ????y (???????????????)",
                              color=discord.Color.from_rgb(255, 165, 158))
        await ctx.send(embed=embed)
        await self.cleanup(ctx.guild)

    @commands.command(name='lyrics', description="l???i b??i h??t")
    async def lyrics_(self, ctx, *, search=None):
        """T??m l???i b??i h??t."""
        if search != None:
            song = genius.search_song(title=search)
            embed = discord.Embed(title=song.title, description=song.lyrics,
                                  color=discord.Color.from_rgb(255, 165, 158))
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(description='Nh???p v?? t??n b??i h??t ??i n??o! ( ???????????)??? ',
                                  color=discord.Color.from_rgb(255, 165, 158))
            await ctx.send(embed=embed)

    @commands.command(name='search', description="t??m b??i h??t tr??n youtube")
    async def search_(self, ctx, *, query=None):
        """T??m ki???m tr??n Youtube."""
        if query != None:

            videosSearch = VideosSearch(query, limit=5).result()['result']

            fmt = '\n'.join(
                f"`{(videosSearch.index(_)) + 1}.` `{_['title'].replace('|', '-')}` | `{_['duration']}`"
                for _ in videosSearch)
            embed = discord.Embed(title=f'K???t qu??? t??m ki???m cho "{query}"', description=fmt,
                                  color=discord.Color.from_rgb(255, 165, 158))
            embed.set_footer(
                text=f"Nh???p v??o l???a ch???n c???a b???n (1-{len(videosSearch)} | 0 ????? hu???):")

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

                embed = discord.Embed(title="", description="???? hu??? t??m ki???m (???????????????)",
                                      color=discord.Color.from_rgb(255, 165, 158))
                await ctx.send(embed=embed)

        else:
            embed = discord.Embed(description='Nh???p v?? t??n b??i h??t ??i n??o! ( ???????????)??? ',
                                  color=discord.Color.from_rgb(255, 165, 158))
            await ctx.send(embed=embed)


class Image(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def cat(self, ctx):
        """Beau s??? g???i cho b???n ???nh m??o cute."""
        response = requests.get('https://aws.random.cat/meow')
        data = response.json()
        await ctx.send(data['file'])

    @commands.command()
    async def dog(self, ctx):
        """Beau s??? g???i cho b???n ???nh ch?? kh?? ??a."""
        response = requests.get('https://dog.ceo/api/breeds/image/random')
        data = response.json()
        await ctx.send(data['message'])


class Game(commands.Cog):
    @commands.command(name='tft', description="l???i l??n trang b??? ??TCL")
    async def tft_(self, ctx, champion: str):
        """Tra c???u l???i l??n trang b??? ??TCL."""
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
            await ctx.send(f'C?? cc m?? {champion}')


class Playlist(commands.Cog):
    @commands.command(name='playlist', aliases=['pl'], description="Thao t??c Playlist")
    async def playlist(self, ctx):
        """Thao t??c Playlist."""
        pls = db.get_playlist(ctx.guild.id)
        if pls:
            fmt = '\n'.join(
                f"`pid:{(_[0])}` `{_[1]}` | ???????c `{_[-1]}` t???o ng??y `{_[3]}`"
                for _ in pls)
            await ctx.send(embed=make_embed(fmt, title=f'Playlist ???? t???o c???a {ctx.guild.name}'))

        else:
            await ctx.send(embed=make_embed('Ko c?? m??? g?? h???t'))

    @commands.command(name='playlist_add', aliases=['pladd'], description="T???o playlist m???i")
    async def playlist_add(self, ctx, *, pname):
        """Th??m playlist."""
        status = db.add_playlist(pname, ctx.guild.id, ctx.author.name)
        if status:
            await ctx.send(embed=make_embed(f'T???o "{pname}" r??i nh??'))
        else:
            await ctx.send(embed=make_embed('L???i m??? n?? r???i nh??'))

    @commands.command(name='playlist_rm', aliases=['plrm'], description="Xo?? playlist")
    async def playlist_rm(self, ctx, *, pid):
        """Xo?? playlist."""
        status = db.rm_playlist(pid)
        if status:
            await ctx.send(embed=make_embed(f'???? xo?? pid = {pid}"'))
        else:
            await ctx.send(embed=make_embed('L???i m??? n?? r???i nh??'))

#############################################################################################


def setup(bot):
    bot.add_cog(Image(bot))
    bot.add_cog(Music(bot))
    bot.add_cog(Playlist(bot))
    bot.add_cog(Game(bot))


bot = commands.Bot(command_prefix=commands.when_mentioned_or("!"),
                   description='C??c l???nh c?? b???n')


@bot.event
async def on_ready():
    if maintain_mode:
        await bot.change_presence(status=discord.Status.do_not_disturb, activity=discord.Activity(type=discord.ActivityType.listening, name="??ang n??ng c???p :("))
    else:
        await bot.change_presence(status=discord.Status.online, activity=discord.Activity(type=discord.ActivityType.watching, name="Batman: Vengeance"))
    print("Bot is ready!")


setup(bot)
bot.run('NjgzNjQ2MzE4MzE2NjE3NzU4.Gv7ha9.6RPmbxoISolSaFhyyv_mUW4EGqtet3vWLhFew8')
