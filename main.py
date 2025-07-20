import os
import json
import asyncio
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from collections import deque
from r2_manager import load_songs, load_playlist, save_playlist
import logging
from random import shuffle as do_shuffle

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
R2_PUBLIC_BASE = os.getenv("R2_PUBLIC_BASE")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix="/", intents=intents)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# === 播放佇列與播放鎖 ===
guild_queues: dict[int, deque[int]] = {}
playing_lock: dict[int, bool] = {}

# === 播放函式 ===
async def play_from_queue(guild: discord.Guild, channel: discord.VoiceChannel):
    guild_id = guild.id
    queue = guild_queues.get(guild_id, deque())
    if not queue:
        logger.info("🈳 佇列為空，略過播放")
        return

    song_id = queue[0]
    url = f"{R2_PUBLIC_BASE}/songs/{song_id}.mp3"
    songs = load_songs()
    song = next((s for s in songs if s["id"] == song_id), None)

    vc = await channel.connect()
    logger.info(f"已連接語音頻道：{channel.name}")
    logger.info(f"正在播放：{song['title']} - {song['artist']} ({url})")

    def after_play(err):
        if err:
            logger.error(f"❌ 播放錯誤：{err}")
        else:
            logger.info("✅ 播放完成")
        coro = handle_song_end(guild)
        fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
        try:
            fut.result()
        except Exception as e:
            logger.error(f"❌ 無法處理播放結束：{e}")

    vc.play(discord.FFmpegPCMAudio(url), after=after_play)


async def handle_song_end(guild):
    guild_id = guild.id
    queue = guild_queues.get(guild_id, deque())
    if queue:
        queue.popleft()
    voice = discord.utils.get(bot.voice_clients, guild=guild)
    if voice:
        await voice.disconnect()
    logger.info("🔇 播放完畢，已斷開語音連線")
    playing_lock[guild_id] = False


# === /play 指令 ===
@bot.tree.command(name="play", description="播放指定歌曲")
@app_commands.describe(title="歌曲標題")
async def play(interaction: discord.Interaction, title: str):
    user = interaction.user
    voice = user.voice
    if not voice or not voice.channel:
        await interaction.response.send_message("❌ 請先加入語音頻道")
        return

    songs = load_songs()
    song = next((s for s in songs if s["title"] == title), None)
    if not song:
        await interaction.response.send_message("❌ 找不到此歌曲")
        return

    guild_id = interaction.guild.id
    queue = guild_queues.setdefault(guild_id, deque())
    queue.append(song["id"])
    await interaction.response.send_message(f"✅ 已加入佇列：{song['title']} - {song['artist']}")

    if not playing_lock.get(guild_id, False):
        playing_lock[guild_id] = True
        await play_from_queue(interaction.guild, voice.channel)


# === /queue 指令 ===
@bot.tree.command(name="queue", description="顯示當前播放佇列")
async def queue(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    queue = guild_queues.get(guild_id, deque())
    if not queue:
        await interaction.response.send_message("📭 播放佇列為空")
        return

    songs = load_songs()
    id_to_song = {s["id"]: s for s in songs}
    lines = ["🎶 當前播放佇列："]
    for i, song_id in enumerate(queue, 1):
        song = id_to_song.get(song_id)
        if song:
            lines.append(f"{i}. {song['title']} - {song['artist']}")
        else:
            lines.append(f"{i}. ❓ 無法識別 ID：{song_id}")
    await interaction.response.send_message("\n".join(lines))


# === /shuffle 指令 ===
@bot.tree.command(name="shuffle", description="將目前播放佇列打亂")
async def shuffle(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    queue = guild_queues.get(guild_id, deque())
    if not queue:
        await interaction.response.send_message("📭 播放佇列為空，無法打亂")
        return

    queue_list = list(queue)
    do_shuffle(queue_list)
    guild_queues[guild_id] = deque(queue_list)
    await interaction.response.send_message("🔀 播放佇列已打亂")


# === /play_playlist 指令 ===
@bot.tree.command(name="play_playlist", description="播放一個歌單中的所有歌曲")
@app_commands.describe(name="歌單名稱")
async def play_playlist(interaction: discord.Interaction, name: str):
    user_id = str(interaction.user.id)
    playlists = load_playlist(user_id)
    if name not in playlists:
        playlists = load_playlist("allsong")  # 嘗試讀取全域歌單
        if name not in playlists:
            await interaction.response.send_message(f"⚠️ 找不到歌單 `{name}`")
            return

    song_ids = playlists[name]
    if not song_ids:
        await interaction.response.send_message(f"📭 歌單 `{name}` 是空的")
        return

    guild_id = interaction.guild.id
    queue = guild_queues.setdefault(guild_id, deque())
    queue.extend(song_ids)

    songs = load_songs()
    id_to_title = {s["id"]: s["title"] for s in songs}
    summary = [id_to_title.get(i, f"❓ID={i}") for i in song_ids[:10]]
    await interaction.response.send_message(f"✅ 已加入佇列 {len(song_ids)} 首：{summary}")

    if not playing_lock.get(guild_id, False):
        playing_lock[guild_id] = True
        voice = interaction.user.voice
        if voice and voice.channel:
            await play_from_queue(interaction.guild, voice.channel)


# === /insert 指令 ===
@bot.tree.command(name="insert", description="插播歌曲，立即放在佇列最前面")
@app_commands.describe(title="歌曲標題")
async def insert(interaction: discord.Interaction, title: str):
    songs = load_songs()
    song = next((s for s in songs if s["title"] == title), None)
    if not song:
        await interaction.response.send_message("❌ 找不到此歌曲")
        return

    voice = interaction.user.voice
    if not voice or not voice.channel:
        await interaction.response.send_message("❌ 請先加入語音頻道")
        return

    guild_id = interaction.guild.id
    queue = guild_queues.setdefault(guild_id, deque())
    queue.appendleft(song["id"])
    await interaction.response.send_message(f"🎯 已插播：{song['title']} - {song['artist']}")

    if not playing_lock.get(guild_id, False):
        playing_lock[guild_id] = True
        await play_from_queue(interaction.guild, voice.channel)


# === /move 指令 ===
@bot.tree.command(name="move", description="將佇列中指定歌曲移到最前面")
@app_commands.describe(position="欲移動的歌曲位置（從 1 開始）")
async def move(interaction: discord.Interaction, position: int):
    guild_id = interaction.guild.id
    queue = guild_queues.get(guild_id, deque())
    if not queue:
        await interaction.response.send_message("⚠️ 目前佇列為空")
        return

    if position < 1 or position > len(queue):
        await interaction.response.send_message("⚠️ 無效的歌曲位置")
        return

    song_id = queue[position - 1]
    del queue[position - 1]
    queue.appendleft(song_id)
    songs = load_songs()
    song = next((s for s in songs if s["id"] == song_id), None)
    title = song["title"] if song else f"ID {song_id}"
    await interaction.response.send_message(f"🔀 已將第 {position} 首《{title}》移至最前面")

# === /skip 指令 ===
@bot.tree.command(name="skip", description="跳過目前播放的歌曲")
async def skip(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        interaction.guild.voice_client.stop()
        await interaction.response.send_message("⏭️ 已跳過目前播放的歌曲")
    else:
        await interaction.response.send_message("⚠️ 沒有正在播放的歌曲")


# === /disconnect 指令 ===
@bot.tree.command(name="disconnect", description="讓機器人離開語音頻道並清空佇列")
async def disconnect(interaction: discord.Interaction):
    guild = interaction.guild
    guild_id = guild.id
    vc = guild.voice_client
    if vc:
        await vc.disconnect()
        guild_queues[guild_id].clear()
        playing_lock[guild_id] = False
        await interaction.response.send_message("👋 已離開語音頻道並清空佇列")
    else:
        await interaction.response.send_message("⚠️ 機器人未連接語音頻道")

# === /remove 指令 ===
@bot.tree.command(name="remove", description="從佇列中移除第 n 首歌曲")
@app_commands.describe(position="欲移除的歌曲位置（從 1 開始）")
async def remove(interaction: discord.Interaction, position: int):
    guild_id = interaction.guild.id
    queue = guild_queues.get(guild_id, deque())
    if not queue:
        await interaction.response.send_message("⚠️ 目前佇列為空")
        return

    if position < 1 or position > len(queue):
        await interaction.response.send_message("⚠️ 無效的歌曲位置")
        return

    song_id = queue[position - 1]
    del queue[position - 1]
    songs = load_songs()
    song = next((s for s in songs if s["id"] == song_id), None)
    title = song["title"] if song else f"ID {song_id}"
    await interaction.response.send_message(f"🗑️ 已移除第 {position} 首歌曲：{title}")


@bot.event
async def on_voice_state_update(member, before, after):
    if member.id != bot.user.id:
        return

    if before.channel and not after.channel:
        guild_id = before.channel.guild.id
        if guild_id in guild_queues:
            guild_queues[guild_id].clear()
            logger.info(f"📤 Bot 被從語音頻道移除，已清空佇列並重置播放鎖（guild_id={guild_id}）")
        playing_lock[guild_id] = False


@bot.event
async def on_ready():
    await bot.tree.sync()
    logger.info(f"✅ 登入成功：{bot.user}")
    logger.info("✅ Slash 指令同步成功（11 個）")

bot.run(TOKEN)
