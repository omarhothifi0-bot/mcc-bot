import os
import re
import asyncio
import traceback
from threading import Thread

import discord
from discord.ext import commands
import yt_dlp
from flask import Flask

# ----------------------------------------------------
# 1. Keep-Alive
# ----------------------------------------------------
app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running 24/7!"


def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))


def keep_alive():
    Thread(target=run_flask, daemon=True).start()


# ----------------------------------------------------
# 2. Intents (بدون members عشان ما تحتاج تفعيل إضافي)
# ----------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True  # لازم يكون مفعّل من Developer Portal
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

song_queues = {}  # guild_id -> list of songs

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "auto",
    "source_address": "0.0.0.0",
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

# مسار FFmpeg: يستخدم النسخة الجاهزة من imageio-ffmpeg إذا موجودة، وإلا يدور على ffmpeg بالنظام
try:
    import imageio_ffmpeg
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_PATH = "ffmpeg"
print(f"🎞️ FFmpeg path: {FFMPEG_PATH}")

# ----------------------------------------------------
# كلمات الأوامر
# ----------------------------------------------------
CMD_COME = {"come", "join", "تعال", "انضم"}
CMD_PLAY = {"شغل", "ش", "play", "p"}
CMD_SKIP = {"سكب", "س", "skip"}
CMD_LEAVE = {"leave", "اطلع", "اخرج", "غادر"}
CMD_PAUSE = {"وقف", "pause"}
CMD_RESUME = {"كمل", "resume"}
CMD_QUEUE = {"قائمة", "queue", "q"}
CMD_PING = {"ping"}

# أوامر تشتغل بدون منشن (بس لما البوت يكون معك بنفس الروم)
NO_MENTION_CMDS = {"شغل", "play", "skip", "سكب", "pause", "وقف", "resume", "كمل", "قائمة", "queue"}

MENTION_RE = re.compile(r"<@[!&]?\d+>")


# ----------------------------------------------------
# تشغيل الأغنية التالية
# ----------------------------------------------------
async def play_next(guild, text_channel):
    queue = song_queues.get(guild.id)
    voice_client = guild.voice_client

    if not queue or not voice_client:
        return

    song = queue.pop(0)

    try:
        source = discord.FFmpegOpusAudio(song["url"], executable=FFMPEG_PATH, **FFMPEG_OPTIONS)
    except Exception as e:
        await text_channel.send(f"❌ ما قدرت أشغل **{song['title']}**: `{e}`")
        await play_next(guild, text_channel)
        return

    def after_playing(error):
        if error:
            print(f"Player error: {error}")
        asyncio.run_coroutine_threadsafe(play_next(guild, text_channel), bot.loop)

    voice_client.play(source, after=after_playing)
    await text_channel.send(f"🎶 جاري تشغيل: **{song['title']}**")


async def extract_song(query):
    loop = asyncio.get_running_loop()
    target = query if query.startswith("http") else f"ytsearch:{query}"
    data = await loop.run_in_executor(
        None, lambda: ytdl.extract_info(target, download=False)
    )
    if "entries" in data:
        if not data["entries"]:
            return None
        data = data["entries"][0]
    return {"url": data["url"], "title": data.get("title", "Unknown")}


async def join_author_channel(message):
    """يدخل البوت لروم الكاتب، ويرجع الـ voice_client أو None."""
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
        await message.channel.send(f"❌ ما قدرت أدخل الروم: `{e}`")
        traceback.print_exc()
        return None
    return vc


# ----------------------------------------------------
# 3. Events
# ----------------------------------------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as: {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(activity=discord.Game(name="@mention + شغل"))


@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    is_mentioned = bot.user in message.mentions

    # نظّف المنشن من النص
    clean = MENTION_RE.sub("", message.content).strip()
    words = clean.lower().split()

    # بدون منشن: الأوامر تشتغل بس إذا البوت بنفس الروم الصوتي تبعك
    if not is_mentioned:
        bot_vc = message.guild.voice_client
        author_channel = message.author.voice.channel if message.author.voice else None
        in_same_room = bot_vc is not None and author_channel is not None and bot_vc.channel == author_channel
        if not (in_same_room and words and words[0] in NO_MENTION_CMDS):
            await bot.process_commands(message)
            return

    if not words:
        await message.channel.send(
            "👋 أنا هون! جرّب: `تعال` / `شغل اسم الأغنية` / `سكب` / `وقف` / `كمل` / `قائمة` / `اطلع`"
        )
        return

    first = words[0]

    try:
        # ---------- come ----------
        if first in CMD_COME:
            vc = await join_author_channel(message)
            if vc:
                await message.channel.send(f"🔊 دخلت الروم: **{vc.channel.name}**")

        # ---------- play ----------
        elif first in CMD_PLAY:
            # اسم الأغنية بالحروف الأصلية (بدون lower)
            original_words = clean.split()
            query = " ".join(original_words[1:]).strip()
            if not query:
                await message.channel.send("⚠️ اكتب اسم الأغنية أو الرابط! مثال: `@البوت شغل اسم الاغنية`")
                return

            vc = await join_author_channel(message)
            if not vc:
                return

            await message.channel.send(f"🔍 جاري البحث عن: **{query}** ...")
            song = await extract_song(query)
            if not song:
                await message.channel.send("❌ ما لقيت نتائج.")
                return

            queue = song_queues.setdefault(message.guild.id, [])
            queue.append(song)

            if vc.is_playing() or vc.is_paused():
                await message.channel.send(f"➕ تمت إضافة: **{song['title']}** للقائمة!")
            else:
                await play_next(message.guild, message.channel)

        # ---------- skip ----------
        elif first in CMD_SKIP:
            vc = message.guild.voice_client
            if vc and vc.is_playing():
                vc.stop()  # الـ after callback بيشغل التالية
                await message.channel.send("⏭️ تم تخطي الأغنية.")
            else:
                await message.channel.send("⚠️ ما في شي شغّال هسا!")

        # ---------- leave ----------
        elif first in CMD_LEAVE:
            vc = message.guild.voice_client
            if vc:
                song_queues.pop(message.guild.id, None)
                await vc.disconnect()
                await message.channel.send("👋 طلعت من الروم ونظفت القائمة.")
            else:
                await message.channel.send("⚠️ البوت مش موجود بأي روم صوتي!")

        # ---------- pause ----------
        elif first in CMD_PAUSE:
            vc = message.guild.voice_client
            if vc and vc.is_playing():
                vc.pause()
                await message.channel.send("⏸️ تم الإيقاف المؤقت.")

        # ---------- resume ----------
        elif first in CMD_RESUME:
            vc = message.guild.voice_client
            if vc and vc.is_paused():
                vc.resume()
                await message.channel.send("▶️ تم الاستئناف.")

        # ---------- queue ----------
        elif first in CMD_QUEUE:
            queue = song_queues.get(message.guild.id, [])
            if not queue:
                await message.channel.send("📭 القائمة فاضية.")
            else:
                lines = [f"{i}. {s['title']}" for i, s in enumerate(queue[:10], 1)]
                await message.channel.send("📜 **القائمة:**\n" + "\n".join(lines))

        # ---------- ping ----------
        elif first in CMD_PING:
            await message.channel.send("🏓 Pong! البوت شغال ومستجيب.")

        else:
            await message.channel.send("🤔 ما فهمت الأمر. جرّب: `تعال` / `شغل` / `سكب` / `وقف` / `كمل` / `قائمة` / `اطلع`")

    except Exception as e:
        traceback.print_exc()
        await message.channel.send(f"❌ صار خطأ: `{e}`")

    await bot.process_commands(message)


# ----------------------------------------------------
# 4. Run
# ----------------------------------------------------
if __name__ == "__main__":
    keep_alive()
    token = os.getenv("DISCORD_TOKEN")
    if token:
        bot.run(token)
    else:
        print("❌ DISCORD_TOKEN is missing!")
