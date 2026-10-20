import os
import re
import time
import shutil
import asyncio
import tempfile
import traceback
import importlib
from threading import Thread

import discord
from discord.ext import commands
import yt_dlp
from flask import Flask

# ====================================================
# 1. Keep Alive
# ====================================================
app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running!"


def keep_alive():
    port = int(os.getenv("PORT", 8080))
    Thread(
        target=lambda: app.run(
            host="0.0.0.0",
            port=port
        ),
        daemon=True
    ).start()


# ====================================================
# 2. الإعدادات
# ====================================================
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

song_queues = {}
guild_volumes = {}

DEFAULT_VOLUME = 0.5

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "auto",
    "source_address": "0.0.0.0",
}

COOKIES_FILE = os.getenv(
    "COOKIES_FILE",
    "cookies.txt"
)

if os.path.exists(COOKIES_FILE):
    cookie_copy = os.path.join(
        tempfile.gettempdir(),
        "yt_cookies.txt"
    )
    shutil.copyfile(COOKIES_FILE, cookie_copy)
    YTDL_OPTIONS["cookiefile"] = cookie_copy
    print("Using cookies file")

DL_DIR = os.path.join(
    tempfile.gettempdir(),
    "musicbot"
)
os.makedirs(DL_DIR, exist_ok=True)

YTDL_OPTIONS["outtmpl"] = os.path.join(
    DL_DIR,
    "%(id)s.%(ext)s"
)

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

FFMPEG_PATH = shutil.which("ffmpeg")

if not FFMPEG_PATH:
    try:
        import imageio_ffmpeg
        FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        FFMPEG_PATH = "ffmpeg"

print(f"FFmpeg: {FFMPEG_PATH}")


# ====================================================
# 3. الأوامر
# ====================================================
CMD_COME = {"come", "join", "تعال", "انضم"}
CMD_PLAY = {"شغل", "ش", "play", "p"}
CMD_SKIP = {"سكب", "س", "skip"}
CMD_LEAVE = {"leave", "اطلع", "اخرج", "غادر"}
CMD_PAUSE = {"وقف", "pause"}
CMD_RESUME = {"كمل", "resume"}
CMD_QUEUE = {"قائمة", "queue", "q"}
CMD_PING = {"ping"}
CMD_DIAG = {"تشخيص", "diag"}
CMD_VOLUME = {"صوت", "volume", "vol"}

NO_MENTION_CMDS = {
    "شغل", "ش", "play", "p",
    "سكب", "س", "skip",
    "وقف", "pause",
    "كمل", "resume",
    "قائمة", "queue", "q",
    "صوت", "volume", "vol",
}

MENTION_RE = re.compile(r"<@[!&]?\d+>")


# ====================================================
# 4. دوال مساعدة
# ====================================================
def get_volume(guild_id):
    return guild_volumes.get(
        guild_id,
        DEFAULT_VOLUME
    )


async def extract_song(query):
    loop = asyncio.get_running_loop()

    target = (
        query
        if query.startswith("http")
        else f"ytsearch:{query}"
    )

    def fetch(url):
        data = ytdl.extract_info(
            url,
            download=True
        )

        if "entries" in data:
            entries = data["entries"]

            if not entries:
                return None

            data = entries[0]

        return {
            "path": ytdl.prepare_filename(data),
            "title": data.get("title", "Unknown"),
        }

    def work():
        try:
            return fetch(target)
        except Exception as e:
            if query.startswith("http"):
                raise

            print(
                f"YouTube failed, trying SoundCloud: {e}"
            )
            return fetch(f"scsearch:{query}")

    return await loop.run_in_executor(
        None,
        work
    )


def remove_file(path):
    try:
        os.remove(path)
    except Exception:
        pass


def build_source(song, guild_id):
    # PCM مع PCMVolumeTransformer حتى يتغير الصوت
    # مباشرةً أثناء تشغيل الأغنية.
    audio = discord.FFmpegPCMAudio(
        song["path"],
        executable=FFMPEG_PATH,
        options="-vn"
    )

    return discord.PCMVolumeTransformer(
        audio,
        volume=get_volume(guild_id)
    )


async def play_next(guild, text_channel):
    queue = song_queues.get(guild.id)
    vc = guild.voice_client

    if not queue or not vc:
        return

    song = queue.pop(0)

    try:
        source = build_source(song, guild.id)
    except Exception as e:
        traceback.print_exc()

        await text_channel.send(
            f"❌ ما قدرت أجهز الصوت لـ "
            f"**{song['title']}**:\n`{e}`"
        )

        remove_file(song["path"])
        await play_next(guild, text_channel)
        return

    started = time.time()

    def after(error):
        if error:
            print(f"Player error: {error}")

        remove_file(song["path"])

        if time.time() - started < 3:
            msg = (
                f"⚠️ الأغنية **{song['title']}** "
                "توقفت بعد ثوانٍ قليلة."
            )

            if error:
                msg += f"\n`{error}`"

            asyncio.run_coroutine_threadsafe(
                text_channel.send(msg),
                bot.loop
            )

        asyncio.run_coroutine_threadsafe(
            play_next(guild, text_channel),
            bot.loop
        )

    try:
        vc.play(source, after=after)
    except Exception as e:
        traceback.print_exc()
        source.cleanup()

        await text_channel.send(
            f"❌ فشل التشغيل: `{e}`"
        )
        remove_file(song["path"])
        return

    percent = round(
        get_volume(guild.id) * 100
    )

    await text_channel.send(
        f"🎶 جاري تشغيل: **{song['title']}**\n"
        f"🔊 الصوت: **{percent}%**"
    )


async def join_author_channel(message):
    if (
        not message.author.voice
        or not message.author.voice.channel
    ):
        await message.channel.send(
            "⚠️ لازم تكون داخل روم صوتي أولًا!"
        )
        return None

    channel = message.author.voice.channel
    vc = message.guild.voice_client

    try:
        if vc:
            if vc.channel != channel:
                await vc.move_to(channel)
        else:
            vc = await channel.connect()

    except Exception as e:
        traceback.print_exc()

        await message.channel.send(
            f"❌ ما قدرت أدخل الروم: `{e}`"
        )
        return None

    return vc


async def run_diagnostics(guild):
    lines = [
        f"discord.py: {discord.__version__}",
        f"FFmpeg: {FFMPEG_PATH}",
        f"Volume: {round(get_volume(guild.id) * 100)}%",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            FFMPEG_PATH,
            "-hide_banner",
            "-version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        out, _ = await proc.communicate()

        version = out.decode(
            errors="ignore"
        ).splitlines()

        if version:
            lines.append(version[0])

    except Exception as e:
        lines.append(f"FFmpeg error: {e}")

    for mod in ("nacl", "davey"):
        try:
            m = importlib.import_module(mod)
            lines.append(
                f"{mod}: OK "
                f"{getattr(m, '__version__', '')}"
            )
        except Exception:
            lines.append(f"{mod}: غير منصّب")

    vc = guild.voice_client

    lines.append(
        "Voice: متصل"
        if vc and vc.is_connected()
        else "Voice: غير متصل"
    )

    return "```\n" + "\n".join(lines) + "\n```"


# ====================================================
# 5. الأحداث والأوامر
# ====================================================
@bot.event
async def on_ready():
    print(
        f"Logged in as: {bot.user} "
        f"(ID: {bot.user.id})"
    )

    await bot.change_presence(
        activity=discord.Game(
            name="@mention + تعال"
        )
    )


@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    is_mentioned = bot.user in message.mentions

    clean = MENTION_RE.sub(
        "",
        message.content
    ).strip()

    parts = clean.split()
    words = clean.lower().split()

    if not is_mentioned:
        bot_vc = message.guild.voice_client

        author_ch = (
            message.author.voice.channel
            if message.author.voice
            else None
        )

        same_room = (
            bot_vc is not None
            and author_ch is not None
            and bot_vc.channel == author_ch
        )

        if not (
            same_room
            and words
            and words[0] in NO_MENTION_CMDS
        ):
            return

    if not words:
        await message.channel.send(
            "👋 الأوامر:\n"
            "`تعال`، `شغل اسم الأغنية`، `سكب`، "
            "`وقف`، `كمل`، `قائمة`، `صوت 10`، `اطلع`"
        )
        return

    cmd = words[0]
    gid = message.guild.id

    try:
        # ---------- دخول الروم ----------
        if cmd in CMD_COME:
            vc = await join_author_channel(message)

            if vc:
                await message.channel.send(
                    f"🔊 دخلت الروم: **{vc.channel.name}**"
                )

        # ---------- تشغيل أغنية ----------
        elif cmd in CMD_PLAY:
            query = " ".join(parts[1:]).strip()

            if not query:
                await message.channel.send(
                    "⚠️ اكتب اسم الأغنية أو الرابط.\n"
                    "مثال: `شغل يا ليلي`"
                )
                return

            vc = await join_author_channel(message)

            if not vc:
                return

            await message.channel.send(
                f"🔍 جاري البحث عن: **{query}**..."
            )

            song = await extract_song(query)

            if not song:
                await message.channel.send(
                    "❌ ما لقيت نتائج."
                )
                return

            song_queues.setdefault(
                gid, []
            ).append(song)

            if vc.is_playing() or vc.is_paused():
                await message.channel.send(
                    f"➕ تمت إضافة **{song['title']}** للقائمة!"
                )
            else:
                await play_next(
                    message.guild,
                    message.channel
                )

        # ---------- التحكم بالصوت ----------
        elif cmd in CMD_VOLUME:
            if len(parts) == 1:
                percent = round(
                    get_volume(gid) * 100
                )

                await message.channel.send(
                    f"🔊 مستوى الصوت الحالي: **{percent}%**\n"
                    "مثال: `صوت 10` أو `صوت 50` أو `صوت 100`"
                )
                return

            try:
                percent = int(parts[1])
            except ValueError:
                await message.channel.send(
                    "⚠️ اكتب رقمًا من 0 إلى 100."
                )
                return

            if not 0 <= percent <= 100:
                await message.channel.send(
                    "⚠️ الصوت يجب أن يكون بين 0 و100."
                )
                return

            guild_volumes[gid] = percent / 100.0

            vc = message.guild.voice_client

            # تغيير الصوت فورًا للأغنية الحالية
            if vc and vc.source:
                source = vc.source

                if isinstance(
                    source,
                    discord.PCMVolumeTransformer
                ):
                    source.volume = percent / 100.0

            await message.channel.send(
                f"🔊 تم تغيير الصوت إلى **{percent}%**."
            )

        # ---------- تخطي ----------
        elif cmd in CMD_SKIP:
            vc = message.guild.voice_client

            if vc and (
                vc.is_playing()
                or vc.is_paused()
            ):
                vc.stop()

                await message.channel.send(
                    "⏭️ تم تخطي الأغنية."
                )
            else:
                await message.channel.send(
                    "⚠️ ما في أغنية شغالة حاليًا!"
                )

        # ---------- الخروج ----------
        elif cmd in CMD_LEAVE:
            vc = message.guild.voice_client

            if vc:
                for song in song_queues.pop(gid, []):
                    remove_file(song["path"])

                await vc.disconnect()

                await message.channel.send(
                    "👋 طلعت من الروم ونظفت القائمة."
                )
            else:
                await message.channel.send(
                    "⚠️ البوت مش موجود بأي روم صوتي!"
                )

        # ---------- إيقاف مؤقت ----------
        elif cmd in CMD_PAUSE:
            vc = message.guild.voice_client

            if vc and vc.is_playing():
                vc.pause()

                await message.channel.send(
                    "⏸️ تم الإيقاف المؤقت."
                )

        # ---------- استئناف ----------
        elif cmd in CMD_RESUME:
            vc = message.guild.voice_client

            if vc and vc.is_paused():
                vc.resume()

                await message.channel.send(
                    "▶️ تم الاستئناف."
                )

        # ---------- قائمة الأغاني ----------
        elif cmd in CMD_QUEUE:
            queue = song_queues.get(gid, [])

            if not queue:
                await message.channel.send(
                    "📭 القائمة فاضية."
                )
            else:
                lines = [
                    f"{i}. {song['title']}"
                    for i, song in enumerate(
                        queue[:10],
                        1
                    )
                ]

                await message.channel.send(
                    "📜 **القائمة:**\n"
                    + "\n".join(lines)
                )

        # ---------- التشخيص ----------
        elif cmd in CMD_DIAG:
            await message.channel.send(
                await run_diagnostics(message.guild)
            )

        # ---------- Ping ----------
        elif cmd in CMD_PING:
            await message.channel.send(
                "🏓 Pong! البوت شغال."
            )

        else:
            await message.channel.send(
                "🤔 ما فهمت الأمر."
            )

    except Exception as e:
        traceback.print_exc()

        await message.channel.send(
            f"❌ صار خطأ: `{e}`"
        )


# ====================================================
# 6. التشغيل
# ====================================================
if __name__ == "__main__":
    keep_alive()

    token = os.getenv("DISCORD_TOKEN")

    if not token:
        print("DISCORD_TOKEN is missing!")
    else:
        bot.run(token)
