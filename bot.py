import os
import re
import time
import shutil
import asyncio
import tempfile
import traceback
from threading import Thread

import discord
from discord.ext import commands
import yt_dlp
from flask import Flask

# ====================================================
# 1. Keep-Alive
# ====================================================
app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running!"


def keep_alive():
    port = int(os.getenv("PORT", 8080))
    Thread(target=lambda: app.run(host="0.0.0.0", port=port), daemon=True).start()


# ====================================================
# 2. الإعدادات
# ====================================================
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

song_queues = {}  # guild_id -> [songs]

# يفضّل صيغة opus عشان نمرّرها لديسكورد بدون ترميز (أخف وما بتحتاج libopus)
YTDL_OPTIONS = {
    "format": "bestaudio[acodec=opus]/bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "auto",
    "source_address": "0.0.0.0",
}
ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

# مسار FFmpeg: النظام أولاً، وبعدها النسخة الجاهزة من imageio-ffmpeg
FFMPEG_PATH = shutil.which("ffmpeg")
if not FFMPEG_PATH:
    try:
        import imageio_ffmpeg
        FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        FFMPEG_PATH = "ffmpeg"
print(f"🎞️ FFmpeg: {FFMPEG_PATH}")

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

# أوامر تشتغل بدون منشن (بس إذا البوت معك بنفس الروم)
NO_MENTION_CMDS = {"شغل", "play", "سكب", "skip", "وقف", "pause", "كمل", "resume", "قائمة", "queue"}

MENTION_RE = re.compile(r"<@[!&]?\d+>")


# ====================================================
# 4. دوال مساعدة
# ====================================================
async def extract_song(query):
    loop = asyncio.get_running_loop()
    target = query if query.startswith("http") else f"ytsearch:{query}"
    data = await loop.run_in_executor(None, lambda: ytdl.extract_info(target, download=False))
    if "entries" in data:
        if not data["entries"]:
            return None
        data = data["entries"][0]
    headers = data.get("http_headers") or {}
    return {
        "url": data["url"],
        "title": data.get("title", "Unknown"),
        "acodec": data.get("acodec") or "",
        "user_agent": headers.get("User-Agent"),
    }


def build_source(song):
    before = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    if song.get("user_agent"):
        before += f' -user_agent "{song["user_agent"]}"'

    errf = tempfile.TemporaryFile()
    kwargs = dict(
        executable=FFMPEG_PATH,
        before_options=before,
        options="-vn",
        stderr=errf,
    )
    # إذا المصدر opus نمرره كما هو بدون إعادة ترميز
    if song["acodec"].startswith("opus"):
        kwargs["codec"] = "opus"

    return discord.FFmpegOpusAudio(song["url"], **kwargs), errf


async def play_next(guild, text_channel):
    queue = song_queues.get(guild.id)
    vc = guild.voice_client
    if not queue or not vc:
        return

    song = queue.pop(0)

    try:
        source, errf = build_source(song)
    except Exception as e:
        traceback.print_exc()
        await text_channel.send(f"❌ ما قدرت أجهز الصوت لـ **{song['title']}**:\n`{e}`")
        await play_next(guild, text_channel)
        return

    started = time.time()

    def after(error):
        ff_err = ""
        try:
            errf.seek(0)
            ff_err = errf.read().decode("utf-8", errors="ignore").strip()[-500:]
            errf.close()
        except Exception:
            pass

        if error:
            print(f"Player error: {error}")
        if ff_err:
            print(f"FFmpeg stderr: {ff_err}")

        if time.time() - started < 3:
            msg = "⚠️ الأغنية وقفت بعد ثواني!"
            if ff_err:
                msg += f"\n```{ff_err}```"
            asyncio.run_coroutine_threadsafe(text_channel.send(msg), bot.loop)

        asyncio.run_coroutine_threadsafe(play_next(guild, text_channel), bot.loop)

    try:
        vc.play(source, after=after)
    except Exception as e:
        traceback.print_exc()
        await text_channel.send(f"❌ فشل التشغيل: `{e}`")
        return

    await text_channel.send(f"🎶 جاري تشغيل: **{song['title']}**")


async def join_author_channel(message):
    if not message.author.voice or not message.author.voice.channel:
        await message.channel.send("⚠️ لازم تكون داخل روم صوتي الأول!")
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
        await message.channel.send(f"❌ ما قدرت أدخل الروم: `{e}`")
        return None
    return vc


# ====================================================
# 5. الأحداث
# ====================================================
@bot.event
async def on_ready():
    print(f"✅ Logged in as: {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(activity=discord.Game(name="@mention + تعال"))


@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    is_mentioned = bot.user in message.mentions
    clean = MENTION_RE.sub("", message.content).strip()
    parts = clean.split()
    words = clean.lower().split()

    # بدون منشن: بس إذا البوت بنفس روم الكاتب وأول كلمة أمر
    if not is_mentioned:
        bot_vc = message.guild.voice_client
        author_ch = message.author.voice.channel if message.author.voice else None
        same_room = bot_vc is not None and author_ch is not None and bot_vc.channel == author_ch
        if not (same_room and words and words[0] in NO_MENTION_CMDS):
            return

    if not words:
        await message.channel.send(
            "👋 أنا هون! الأوامر: `تعال` / `شغل اسم الأغنية` / `سكب` / `وقف` / `كمل` / `قائمة` / `اطلع`"
        )
        return

    cmd = words[0]
    gid = message.guild.id

    try:
        # ---------- تعال ----------
        if cmd in CMD_COME:
            vc = await join_author_channel(message)
            if vc:
                await message.channel.send(f"🔊 دخلت الروم: **{vc.channel.name}**")

        # ---------- شغل ----------
        elif cmd in CMD_PLAY:
            query = " ".join(parts[1:]).strip()
            if not query:
                await message.channel.send("⚠️ اكتب اسم الأغنية أو الرابط! مثال: `شغل يا ليلي`")
                return

            vc = await join_author_channel(message)
            if not vc:
                return

            await message.channel.send(f"🔍 جاري البحث عن: **{query}** ...")
            song = await extract_song(query)
            if not song:
                await message.channel.send("❌ ما لقيت نتائج.")
                return

            song_queues.setdefault(gid, []).append(song)

            if vc.is_playing() or vc.is_paused():
                await message.channel.send(f"➕ تمت إضافة: **{song['title']}** للقائمة!")
            else:
                await play_next(message.guild, message.channel)

        # ---------- سكب ----------
        elif cmd in CMD_SKIP:
            vc = message.guild.voice_client
            if vc and (vc.is_playing() or vc.is_paused()):
                vc.stop()
                await message.channel.send("⏭️ تم تخطي الأغنية.")
            else:
                await message.channel.send("⚠️ ما في شي شغّال هسا!")

        # ---------- اطلع ----------
        elif cmd in CMD_LEAVE:
            vc = message.guild.voice_client
            if vc:
                song_queues.pop(gid, None)
                await vc.disconnect()
                await message.channel.send("👋 طلعت من الروم ونظفت القائمة.")
            else:
                await message.channel.send("⚠️ البوت مش موجود بأي روم صوتي!")

        # ---------- وقف ----------
        elif cmd in CMD_PAUSE:
            vc = message.guild.voice_client
            if vc and vc.is_playing():
                vc.pause()
                await message.channel.send("⏸️ تم الإيقاف المؤقت.")

        # ---------- كمل ----------
        elif cmd in CMD_RESUME:
            vc = message.guild.voice_client
            if vc and vc.is_paused():
                vc.resume()
                await message.channel.send("▶️ تم الاستئناف.")

        # ---------- قائمة ----------
        elif cmd in CMD_QUEUE:
            queue = song_queues.get(gid, [])
            if not queue:
                await message.channel.send("📭 القائمة فاضية.")
            else:
                lines = [f"{i}. {s['title']}" for i, s in enumerate(queue[:10], 1)]
                await message.channel.send("📜 **القائمة:**\n" + "\n".join(lines))

        # ---------- ping ----------
        elif cmd in CMD_PING:
            await message.channel.send("🏓 Pong! البوت شغال.")

        else:
            await message.channel.send("🤔 ما فهمت الأمر.")

    except Exception as e:
        traceback.print_exc()
        await message.channel.send(f"❌ صار خطأ: `{e}`")


# ====================================================
# 6. التشغيل
# ====================================================
if __name__ == "__main__":
    keep_alive()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("❌ DISCORD_TOKEN is missing!")
    else:
        bot.run(token)
