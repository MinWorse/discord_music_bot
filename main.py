import os
import json
import asyncio
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from collections import defaultdict, deque
from r2_manager import load_songs, load_playlist
import logging

# 設定 log 格式與等級
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# === 初始化 ===
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix="/", intents=intents)

guild_queues = defaultdict(deque)  # guild_id -> deque of song ids
playing_flag = defaultdict(lambda: False)  # guild_id -> 是否正在播放
now_playing = defaultdict(lambda: None)  # guild_id -> 正在播放的 song_id

# === 播放歌曲邏輯 ===
async def play_from_queue(guild: discord.Guild, channel: discord.VoiceChannel):
    guild_id = guild.id

    if playing_flag[guild_id]:
        logger.info(f"🎵 正在播放中，略過自動播放（guild_id={guild_id}）")
        return

    if not guild_queues[guild_id]:
        logger.info(f"📭 播放佇列為空，無歌曲可播（guild_id={guild_id}）")
        return

    song_id = guild_queues[guild_id].popleft()
    now_playing[guild_id] = song_id
    playing_flag[guild_id] = True

    songs_data = load_songs()
    song = next((s for s in songs_data if s["id"] == song_id), None)

    if not song:
        logger.warning(f"❌ 找不到歌曲 ID={song_id}，跳過（guild_id={guild_id}）")
        playing_flag[guild_id] = False
        await play_from_queue(guild, channel)
        return

    url = song["url"]
    title = song["title"]
    artist = song["artist"]

    logger.info(f"🔊 正在播放：{title} - {artist} ({url})")

    voice_client = guild.voice_client
    if not voice_client:
        voice_client = await channel.connect()

    def after_playing(error):
        if error:
            logger.error(f"❌ 播放錯誤：{error}")
        else:
            logger.info("✅ 播放完成")
        coro = handle_song_end(guild)
        fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
        fut.result()

    voice_client.play(discord.FFmpegPCMAudio(url), after=after_playing)


async def handle_song_end(guild: discord.Guild):
    guild_id = guild.id
    voice_client = guild.voice_client

    playing_flag[guild_id] = False
    now_playing[guild_id] = None

    if not guild_queues[guild_id]:
        if voice_client:
            await voice_client.disconnect()
            logger.info("🔇 播放完畢，已斷開語音連線")
    else:
        await play_from_queue(guild, voice_client.channel)


# === /play 指令 ===
@bot.tree.command(name="play", description="播放指定歌曲")
@app_commands.describe(song_id="輸入歌曲 ID（整數）")
async def play(interaction: discord.Interaction, song_id: int):
    guild_id = interaction.guild.id
    member = interaction.user

    if not member.voice or not member.voice.channel:
        await interaction.response.send_message("⚠️ 請先加入語音頻道")
        return

    songs_data = load_songs()
    song = next((s for s in songs_data if s["id"] == song_id), None)

    if not song:
        await interaction.response.send_message("❌ 找不到該歌曲 ID")
        return

    guild_queues[guild_id].append(song_id)
    logger.info(f"➕ 加入歌曲至佇列：{song['title']}（guild_id={guild_id}）")

    await interaction.response.send_message(f"🎶 已加入佇列：{song['title']}")

    if not playing_flag[guild_id]:
        await play_from_queue(interaction.guild, member.voice.channel)


# === /play_playlist 指令 ===
@bot.tree.command(name="play_playlist", description="播放指定歌單")
@app_commands.describe(name="歌單名稱")
async def play_playlist(interaction: discord.Interaction, name: str):
    guild_id = interaction.guild.id
    member = interaction.user

    if not member.voice or not member.voice.channel:
        await interaction.response.send_message("⚠️ 請先加入語音頻道")
        return

    playlist = load_playlist(member.id, name)
    if not playlist:
        await interaction.response.send_message("❌ 找不到該歌單或為空")
        return

    guild_queues[guild_id].extend(playlist)
    logger.info(f"📥 已加入歌單 {name}（{len(playlist)} 首）至佇列（guild_id={guild_id}）")

    await interaction.response.send_message(f"📥 已加入 {len(playlist)} 首歌曲至佇列！")

    if not playing_flag[guild_id]:
        await play_from_queue(interaction.guild, member.voice.channel)


# === /skip 指令 ===
@bot.tree.command(name="skip", description="跳過當前播放的歌曲")
async def skip(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    voice_client = interaction.guild.voice_client

    if not voice_client or not voice_client.is_playing():
        await interaction.response.send_message("⚠️ 沒有正在播放的歌曲")
        return

    voice_client.stop()
    await interaction.response.send_message("⏭️ 已跳過當前歌曲")
    logger.info(f"⏭️ 跳過當前歌曲（guild_id={guild_id}）")


# === /disconnect 指令 ===
@bot.tree.command(name="disconnect", description="讓機器人離開語音頻道並清空播放佇列")
async def disconnect(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    voice_client = interaction.guild.voice_client

    if not voice_client or not voice_client.is_connected():
        await interaction.response.send_message("⚠️ 機器人未連接語音頻道")
        return

    await voice_client.disconnect()
    guild_queues[guild_id].clear()
    now_playing[guild_id] = None
    playing_flag[guild_id] = False

    await interaction.response.send_message("📴 已離開語音頻道並清空佇列")
    logger.info(f"📴 離開語音並清空佇列（guild_id={guild_id}）")


# === /queue 指令 ===
@bot.tree.command(name="queue", description="顯示目前播放佇列")
async def queue(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    queue = guild_queues[guild_id]
    songs_data = load_songs()
    id_to_song = {song["id"]: song for song in songs_data}

    if not queue:
        await interaction.response.send_message("📭 播放佇列為空")
        return

    lines = ["📑 接下來播放："]
    for i, song_id in enumerate(queue, 1):
        song = id_to_song.get(song_id)
        if song:
            lines.append(f"{i}. {song['title']} - {song['artist']}")
        else:
            lines.append(f"{i}. ❓ 找不到歌曲 ID：{song_id}")
            logger.warning(f"⚠️ queue 顯示找不到歌曲 ID：{song_id}")

    await interaction.response.send_message("\n".join(lines))


# === 啟動事件 ===
@bot.event
async def on_ready():
    await bot.tree.sync()
    logger.info(f"✅ 登入成功：{bot.user}")
    logger.info("✅ Slash 指令同步成功（11 個）")


if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("❌ 環境變數 DISCORD_TOKEN 未設定")
    bot.run(TOKEN)
