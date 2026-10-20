import os
import asyncio
import discord
from discord.ext import commands
import yt_dlp
from flask import Flask
from threading import Thread

# ----------------------------------------------------
# 1. إعداد السيرفر الوهمي (Keep-Alive) لإبقاء البوت شغال
# ----------------------------------------------------
app = Flask('')

@app.route('/')
def home():
    return "Bot is running 24/7!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

# ----------------------------------------------------
# 2. إعداد الـ Intents والصلاحيات المطلوبة للديسكورد
# ----------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True  # ضروري جداً لقراءة محتوى الرسائل
intents.guilds = True
intents.voice_states = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# قائمة انتظار الصوت لكل سيرفر
song_queues = {}

# إعدادات yt-dlp و FFmpeg
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'extractaudio': True,
    'audioformat': 'mp3',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

async def play_next(ctx):
    guild_id = ctx.guild.id
    if guild_id in song_queues and len(song_queues[guild_id]) > 0:
        next_song = song_queues[guild_id].pop(0)
        voice_client = ctx.guild.voice_client
        if voice_client:
            source = await discord.FFmpegOpusAudio.from_probe(next_song['url'], **FFMPEG_OPTIONS)
            voice_client.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
            await ctx.send(f"🎶 جاري تشغيل: **{next_song['title']}**")
    else:
        pass

# ----------------------------------------------------
# 3. أحداث البوت (Events)
# ----------------------------------------------------
@bot.event
async def on_ready():
    print(f'✅ Logged in successfully as: {bot.user.name} (ID: {bot.user.id})')
    await bot.change_presence(activity=discord.Game(name="جاهز للتشغيل | @mention"))

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    content = message.content.strip().lower()
    is_mentioned = bot.user.mentioned_in(message)

    # ------------------------------------------------
    # 1. أمر الدخول (come / تعال / انضم)
    # ------------------------------------------------
    if is_mentioned and any(word in content for word in ["come", "تعال", "انضم"]):
        if message.author.voice:
            channel = message.author.voice.channel
            if message.guild.voice_client:
                await message.guild.voice_client.move_to(channel)
            else:
                await channel.connect()
            await message.channel.send(f"🔊 دخلت الروم: **{channel.name}**")
        else:
            await message.channel.send("⚠️ لازم تكون داخل روم صوتي الأول!")

    # ------------------------------------------------
    # 2. أمر التشغيل (شغل / ش / play / p)
    # ------------------------------------------------
    elif is_mentioned and any(word in content.split() for word in ["شغل", "ش", "play", "p"]):
        if not message.author.voice:
            await message.channel.send("⚠️ لازم تكون داخل روم صوتي عشان تشغل أغنية!")
            return

        # استخراج اسم الأغنية بعد المنشن والكلمة المفتاحية
        words = content.split()
        search_query = " ".join([w for w in words if not w.startswith("<@") and w not in ["شغل", "ش", "play", "p"]])

        if not search_query:
            await message.channel.send("⚠️ اكتب اسم الأغنية أو الرابط بعد الأمر! مثال: `@بوت شغل اسم الاغنية`")
            return

        # الانضمام للروم إذا لم يكن فيه
        voice_client = message.guild.voice_client
        if not voice_client:
            voice_client = await message.author.voice.channel.connect()

        await message.channel.send(f"🔍 جاري البحث عن: **{search_query}** ...")

        # الحصول على رابط الصوت
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(f"ytsearch:{search_query}", download=False))
        
        if 'entries' in data and len(data['entries']) > 0:
            info = data['entries'][0]
        else:
            info = data

        song_info = {
            'url': info['url'],
            'title': info['title']
        }

        guild_id = message.guild.id
        if guild_id not in song_queues:
            song_queues[guild_id] = []

        if voice_client.is_playing() or voice_client.is_paused():
            song_queues[guild_id].append(song_info)
            await message.channel.send(f"➕ تم إضافة: **{song_info['title']}** للقائمة!")
        else:
            song_queues[guild_id].append(song_info)
            await play_next(message)

    # ------------------------------------------------
    # 3. أمر التخطي (سكب / س / skip)
    # ------------------------------------------------
    elif is_mentioned and any(word in content.split() for word in ["سكب", "س", "skip"]):
        voice_client = message.guild.voice_client
        if voice_client and voice_client.is_playing():
            voice_client.stop()
            await message.channel.send("⏭️ تم تخطي الأغنية.")
        else:
            await message.channel.send("⚠️ ما في شي شغّال هسا عشان أعمل له سكب!")

    # ------------------------------------------------
    # 4. أمر الخروج (leave / اطلع / اخرج)
    # ------------------------------------------------
    elif is_mentioned and any(word in content.split() for word in ["leave", "اطلع", "اخرج", "غادر"]):
        voice_client = message.guild.voice_client
        if voice_client:
            await voice_client.disconnect()
            if message.guild.id in song_queues:
                song_queues[message.guild.id].clear()
            await message.channel.send("👋 طلعت من الروم ونظفت القائمة.")
        else:
            await message.channel.send("⚠️ البوت مش موجود بأي روم صوتي!")

    # ------------------------------------------------
    # 5. أمر إيقاف مؤقت / استئناف (وقف / كمل / pause / resume)
    # ------------------------------------------------
    elif is_mentioned and any(word in content.split() for word in ["وقف", "pause"]):
        voice_client = message.guild.voice_client
        if voice_client and voice_client.is_playing():
            voice_client.pause()
            await message.channel.send("⏸️ تم الإيقاف المؤقت.")

    elif is_mentioned and any(word in content.split() for word in ["كمل", "resume"]):
        voice_client = message.guild.voice_client
        if voice_client and voice_client.is_paused():
            voice_client.resume()
            await message.channel.send("▶️ تم الاستئناف.")

    # ------------------------------------------------
    # 6. أمر التجربة السريع (ping)
    # ------------------------------------------------
    elif is_mentioned and "ping" in content:
        await message.channel.send("🏓 Pong! البوت شغال ومستجيب 100%.")

    await bot.process_commands(message)

# ----------------------------------------------------
# 4. تشغيل البوت
# ----------------------------------------------------
if __name__ == "__main__":
    keep_alive()
    token = os.getenv("DISCORD_TOKEN")
    if token:
        bot.run(token)
    else:
        print("❌ DISCORD_TOKEN is missing!")
