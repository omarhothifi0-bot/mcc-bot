import asyncio
from threading import Thread
import discord
from discord.ext import commands
from flask import Flask
import os
import yt_dlp

# --- 1. سيرفر Web مصغر عشان Render ما ينيّم البوت ---
app = Flask("")


@app.route("/")
def home():
  return "Bot is alive and running 24/7!"


def run_flask():
  app.run(host="0.0.0.0", port=8080)


def keep_alive():
  t = Thread(target=run_flask)
  t.start()


# --- 2. إعدادات البوت والـ Intents ---
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True


async def get_prefix(bot, message):
  return [f"<@{bot.user.id}> ", f"<@!{bot.user.id}> "]


bot = commands.Bot(command_prefix=get_prefix, intents=intents)

# قوائم الانتظار لكل سيرفر
queues = {}

ytdl_format_options = {
    "format": "bestaudio/best",
    "restrictfilenames": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}

ffmpeg_options = {
    "before_options": (
        "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    ),
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)


class YTDLSource(discord.PCMVolumeTransformer):

  def __init__(self, source, *, data, requester, volume=0.5):
    super().__init__(source, volume)
    self.data = data
    self.title = data.get("title")
    self.url = data.get("url")
    self.duration = data.get("duration")
    self.thumbnail = data.get("thumbnail")
    self.requester = requester

  @classmethod
  async def from_url(cls, url, *, loop=None, stream=True, requester=None):
    loop = loop or asyncio.get_event_loop()
    data = await loop.run_in_executor(
        None, lambda: ytdl.extract_info(url, download=not stream)
    )
    if "entries" in data and data["entries"]:
      data = data["entries"][0]
    filename = data["url"] if stream else ytdl.prepare_filename(data)
    return cls(
        discord.FFmpegPCMAudio(filename, **ffmpeg_options),
        data=data,
        requester=requester,
    )


@bot.event
async def on_ready():
  print(f"Logged in as {bot.user.name} - جاهز للعمل 24/7!")


# --- الأوامر الأساسية ---


# دخول الروم
@bot.command(name="come")
async def come(ctx):
  if ctx.author.voice:
    channel = ctx.author.voice.channel
    if ctx.voice_client is not None:
      await ctx.voice_client.move_to(channel)
    else:
      await channel.connect()
    embed = discord.Embed(
        title="🎵 تم الانضمام",
        description=(
            f"أبشر! دخلت روم: **{channel.name}** ومستمر معك 24/7."
        ),
        color=discord.Color.green(),
    )
    await ctx.send(embed=embed)
  else:
    await ctx.send("❌ لازم تكون شابك بروم صوتي الأول!")


# الخروج من الروم
@bot.command(name="leave")
async def leave(ctx):
  if ctx.voice_client:
    if ctx.guild.id in queues:
      queues[ctx.guild.id].clear()
    await ctx.voice_client.disconnect()
    await ctx.send("👋 تم الخروج من الروم وتفريغ قائمة الانتظار.")
  else:
    await ctx.send("❌ أنا مش شابك بأي روم صوتي!")


# تشغيل الأغنية التالية
def play_next(ctx):
  guild_id = ctx.guild.id
  if guild_id in queues and len(queues[guild_id]) > 0:
    next_item = queues[guild_id].pop(0)
    asyncio.run_coroutine_threadsafe(
        play_song(ctx, next_item["search"], next_item["requester"]), bot.loop
    )


async def play_song(ctx, search, requester):
  try:
    async with ctx.typing():
      player = await YTDLSource.from_url(
          search, loop=bot.loop, stream=True, requester=requester
      )
      ctx.voice_client.play(
          player,
          after=lambda e: (
              print(f"Player error: {e}") if e else play_next(ctx)
          ),
      )

    embed = discord.Embed(
        title="🎶 شغال الحين",
        description=f"**[{player.title}]({player.url})**",
        color=discord.Color.blue(),
    )
    if player.thumbnail:
      embed.set_thumbnail(url=player.thumbnail)
    embed.add_field(name="طلب بواسطة", value=requester.mention, inline=True)
    await ctx.send(embed=embed)

  except Exception as e:
    await ctx.send(f"❌ حدث خطأ أثناء تشغيل الأغنية: {e}")
    play_next(ctx)


# أمر التشغيل (شغل أو ش)
@bot.command(name="شغل", aliases=["ش"])
async def play(ctx, *, search: str):
  if not ctx.voice_client:
    if ctx.author.voice:
      await ctx.author.voice.channel.connect()
    else:
      await ctx.send("❌ لازم تكون شابك بروم صوتي أول!")
      return

  guild_id = ctx.guild.id
  if guild_id not in queues:
    queues[guild_id] = []

  if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
    queues[guild_id].append({"search": search, "requester": ctx.author})
    embed = discord.Embed(
        title="📥 تمت الإضافة لقائمة الانتظار",
        description=f"البحث عن: **{search}**",
        color=discord.Color.gold(),
    )
    embed.set_footer(
        text=f"ترتيبها بالقائمة: #{len(queues[guild_id])} | طلب بواسطة:"
        f" {ctx.author.display_name}"
    )
    await ctx.send(embed=embed)
  else:
    await play_song(ctx, search, ctx.author)


# أمر التخطي (س أو سكب)
@bot.command(name="سكب", aliases=["س"])
async def skip(ctx):
  if ctx.voice_client and ctx.voice_client.is_playing():
    ctx.voice_client.stop()
    await ctx.send("⏭️ تم تخطي الأغنية الحالية.")
  else:
    await ctx.send("❌ ما في أغنية شغالة حالياً عشان أعمل لها سكب!")


# إيقاف مؤقت (وقف)
@bot.command(name="وقف")
async def pause(ctx):
  if ctx.voice_client and ctx.voice_client.is_playing():
    ctx.voice_client.pause()
    await ctx.send("⏸️ تم الإيقاف المؤقت.")
  else:
    await ctx.send("❌ ما في شيء شغال حالياً!")


# استكمال التشغيل (كمل)
@bot.command(name="كمل")
async def resume(ctx):
  if ctx.voice_client and ctx.voice_client.is_paused():
    ctx.voice_client.resume()
    await ctx.send("▶️ تم استكمال التشغيل.")
  else:
    await ctx.send("❌ الأغنية مش موقوفة مؤقتاً!")


# عرض القائمة (قائمة أو q)
@bot.command(name="قائمة", aliases=["q"])
async def queue(ctx):
  guild_id = ctx.guild.id
  if guild_id in queues and len(queues[guild_id]) > 0:
    description = ""
    for i, item in enumerate(queues[guild_id], 1):
      description += (
          f"**{i}.** {item['search']} — (طلب: {item['requester'].mention})\n"
      )

    embed = discord.Embed(
        title="📋 قائمة الانتظار الحالية",
        description=description,
        color=discord.Color.purple(),
    )
    await ctx.send(embed=embed)
  else:
    await ctx.send("📜 قائمة الانتظار فارغة حالياً.")


# مسح القائمة (تنظيف أو clear)
@bot.command(name="تنظيف", aliases=["clear"])
async def clear_queue(ctx):
  guild_id = ctx.guild.id
  if guild_id in queues:
    queues[guild_id].clear()
  await ctx.send("🧹 تم مسح جميع الأغاني من قائمة الانتظار.")


# تشغيل سيرفر الـ Flask والبوت
keep_alive()
bot.run(os.getenv("DISCORD_TOKEN"))
