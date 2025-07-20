import os
import json
import asyncio
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from collections import defaultdict, deque
from r2_manager import load_songs, load_playlist, save_playlist
import logging
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# === 環境變數 ===
load_dotenv()
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
R2_PUBLIC_BASE = os.getenv("R2_PUBLIC_BASE")

# === 播放佇列管理 ===
guild_queues = defaultdict(deque)      # guild.id -> deque of songs
playing_lock = defaultdict(lambda: False)  # guild.id -> is playing

def find_song_by_title(title):
    for song in load_songs():
        if song["title"] == title:
            return song
    return None

# === Bot 建立 ===
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)

# === Autocomplete ===
async def song_title_autocomplete(interaction: discord.Interaction, current: str):
    try:
        songs = load_songs()
        current_lower = current.lower()
        matches = [
            song for song in songs
            if current_lower in song["title"].lower() or current_lower in song["artist"].lower()
        ]
        return [
            app_commands.Choice(name=f"{s['title']} - {s['artist']}", value=s['title'])
            for s in matches[:25]
        ]
    except Exception as e:
        print(f"❌ Autocomplete 錯誤：{e}")
        return []

# === ✅ 修正 autocomplete：加入 global_playlists.json ===
async def playlist_name_autocomplete(interaction: discord.Interaction, current: str):
    user_id = str(interaction.user.id)
    playlists = load_playlist(user_id)

    try:
        global_playlists = load_playlist("global_playlists")
        playlists.update(global_playlists)
    except Exception as e:
        print(f"⚠️ 無法載入全域歌單：{e}")

    current_lower = current.lower()
    matches = [name for name in playlists if current_lower in name.lower()]
    return [app_commands.Choice(name=name, value=name) for name in matches[:25]]


# === 撥放邏輯 ===
async def play_from_queue(guild: discord.Guild, channel: discord.VoiceChannel):
    guild_id = guild.id
    queue = guild_queues[guild_id]

    try:
        vc = await channel.connect()
        logger.info(f"已連接語音頻道：{channel.name}")
    except discord.ClientException:
        vc = discord.utils.get(bot.voice_clients, guild=guild)
        logger.info("已使用現有語音連線")

    while queue:
        song = queue.popleft()
        url = f"{R2_PUBLIC_BASE}/songs/{song['id']}.mp3"
        logger.info(f"正在播放：{song['title']} - {song['artist']} ({url})")

        vc.play(discord.FFmpegPCMAudio(url), after=lambda e: logger.info("✅ 播放完成") if not e else logger.error(f"❌ 播放錯誤：{e}"))

        try:
            text_channel = discord.utils.get(guild.text_channels, name="general")
            if text_channel:
                await text_channel.send(f"🎧 播放中：{song['title']} - {song['artist']}")
        except Exception as e:
            logger.warning(f"⚠️ 傳送播放訊息失敗：{e}")

        while vc.is_playing():
            await asyncio.sleep(1)

    await vc.disconnect()
    playing_lock[guild_id] = False
    logger.info("🔇 播放完畢，已斷開語音連線")

@bot.event
async def on_voice_state_update(member, before, after):
    if member.id != bot.user.id:
        return

    # 如果 bot 原本在頻道（before.channel != None），但現在已不在（after.channel == None）
    if before.channel and not after.channel:
        guild_id = before.channel.guild.id
        guild_queues[guild_id].clear()
        playing_lock[guild_id] = False
        logger.info(f"📤 Bot 被從語音頻道移除，已清空佇列並重置播放鎖（guild_id={guild_id}）")

# === /show_playlist 指令 ===
@bot.tree.command(name="show_playlist", description="顯示你的某個歌單內容")
@app_commands.describe(name="歌單名稱")
@app_commands.autocomplete(name=playlist_name_autocomplete)
async def show_playlist(interaction: discord.Interaction, name: str):
    user_id = str(interaction.user.id)
    logger.info(f"/show_playlist 執行者：{user_id}，目標歌單：{name}")
    playlists = load_playlist(user_id)

    if name not in playlists:
        logger.info("未在個人歌單中找到，嘗試讀取全域歌單")
        try:
            global_playlists = load_playlist("global_playlists")
            playlists.update(global_playlists)
        except Exception as e:
            logger.warning(f"❌ 載入全域歌單失敗：{e}")
            await interaction.response.send_message(f"⚠️ 找不到歌單 `{name}`")
            return

    if name not in playlists:
        await interaction.response.send_message(f"⚠️ 找不到歌單 `{name}`")
        logger.warning("❌ 無法找到指定歌單")
        return

    song_ids = playlists[name]
    if not song_ids:
        await interaction.response.send_message(f"📭 歌單 `{name}` 是空的")
        logger.info("🔎 歌單為空")
        return

    songs_data = load_songs()
    id_to_song = {int(song["id"]): song for song in songs_data}

    lines = [f"📑 歌單 `{name}` 內容："]
    for i, song_id in enumerate(song_ids, 1):
        song = id_to_song.get(song_id)
        if song:
            lines.append(f"{i}. {song['title']} - {song['artist']}")
        else:
            lines.append(f"{i}. ❓ 無法找到歌曲 ID：{song_id}")
            logger.warning(f"⚠️ 歌單中找不到歌曲 ID：{song_id}")

    await interaction.response.send_message("\n".join(lines))
    logger.info("✅ 歌單內容已發送")

# === /disconnect 指令 ===
@bot.tree.command(name="disconnect", description="讓機器人離開語音頻道並清空佇列")
async def disconnect(interaction: discord.Interaction):
    guild = interaction.guild
    guild_id = guild.id

    vc = discord.utils.get(bot.voice_clients, guild=guild)
    if vc:
        await vc.disconnect()
        logger.info(f"🛑 已離開語音頻道：{vc.channel.name}")
    else:
        await interaction.response.send_message("⚠️ 機器人未連接語音頻道")
        return

    guild_queues[guild_id].clear()
    playing_lock[guild_id] = False
    logger.info("🧹 已清空播放佇列並解除播放鎖定")
    await interaction.response.send_message("👋 已離開語音頻道，佇列已清空")

# === /insert 指令 ===
@bot.tree.command(name="insert", description="插播歌曲，立即放在佇列最前面")
@app_commands.describe(title="歌曲標題")
@app_commands.autocomplete(title=song_title_autocomplete)
async def insert(interaction: discord.Interaction, title: str):
    song = find_song_by_title(title)
    if not song:
        await interaction.response.send_message("❌ 找不到此歌曲")
        return

    voice = interaction.user.voice
    if not voice or not voice.channel:
        await interaction.response.send_message("❌ 請先加入語音頻道")
        return

    guild_id = interaction.guild.id
    guild_queues[guild_id].appendleft(song)
    await interaction.response.send_message(f"🎯 已插播：{song['title']} - {song['artist']}")

    if not playing_lock[guild_id]:
        playing_lock[guild_id] = True
        await play_from_queue(interaction.guild, voice.channel)

# === /move 指令 ===
@bot.tree.command(name="move", description="將佇列中指定歌曲移到最前面")
@app_commands.describe(position="欲移動的歌曲位置（從 1 開始）")
async def move(interaction: discord.Interaction, position: int):
    guild_id = interaction.guild.id
    queue = guild_queues[guild_id]
    if not queue:
        await interaction.response.send_message("⚠️ 目前佇列為空")
        return

    if position < 1 or position > len(queue):
        await interaction.response.send_message("⚠️ 無效的歌曲位置")
        return

    song = queue[position - 1]
    del queue[position - 1]
    queue.appendleft(song)
    await interaction.response.send_message(f"🔀 已將第 {position} 首《{song['title']}》移至最前面")

# === /shuffle 指令 ===
@bot.tree.command(name="shuffle", description="將目前播放佇列打亂")
async def shuffle(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    queue = guild_queues[guild_id]
    if not queue:
        await interaction.response.send_message("📭 播放佇列為空，無法打亂")
        return

    from random import shuffle as do_shuffle
    queue_list = list(queue)
    do_shuffle(queue_list)
    guild_queues[guild_id] = deque(queue_list)
    await interaction.response.send_message("🔀 播放佇列已打亂")

# === /remove 指令 ===
@bot.tree.command(name="remove", description="從佇列中移除第 n 首歌曲")
@app_commands.describe(position="欲移除的歌曲位置（從 1 開始）")
async def remove(interaction: discord.Interaction, position: int):
    guild_id = interaction.guild.id
    queue = guild_queues[guild_id]
    if not queue:
        await interaction.response.send_message("⚠️ 目前佇列為空")
        return

    if position < 1 or position > len(queue):
        await interaction.response.send_message("⚠️ 無效的歌曲位置")
        return

    song = queue[position - 1]
    del queue[position - 1]
    await interaction.response.send_message(f"🗑️ 已移除第 {position} 首歌曲：{song['title']} - {song['artist']}")  

# === /queue 指令 ===
@bot.tree.command(name="queue", description="查看目前播放佇列")
async def show_queue(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    queue = list(guild_queues[guild_id])

    if not playing_lock[guild_id] and not queue:
        await interaction.response.send_message("📭 播放佇列為空")
        return

    message_lines = ["📋 播放佇列："]
    for i, song in enumerate(queue, 1):
        message_lines.append(f"{i}. {song['title']} - {song['artist']}")

    await interaction.response.send_message("\n".join(message_lines))

# === /play 指令 ===
@bot.tree.command(name="play", description="播放歌曲或加入佇列")
@app_commands.describe(title="歌曲標題")
@app_commands.autocomplete(title=song_title_autocomplete)
async def play(interaction: discord.Interaction, title: str):
    song = find_song_by_title(title)
    if not song:
        await interaction.response.send_message("❌ 找不到此歌曲")
        return

    guild = interaction.guild
    user = interaction.user
    voice = user.voice

    if not voice or not voice.channel:
        await interaction.response.send_message("❌ 請先加入語音頻道")
        return

    guild_id = guild.id
    guild_queues[guild_id].append(song)
    await interaction.response.send_message(f"✅ 已加入佇列：{song['title']} - {song['artist']}")

    if not playing_lock[guild_id]:
        playing_lock[guild_id] = True
        await play_from_queue(guild, voice.channel)

# === /skip 指令 ===
@bot.tree.command(name="skip", description="跳過目前歌曲")
async def skip(interaction: discord.Interaction):
    vc = discord.utils.get(bot.voice_clients, guild=interaction.guild)
    if not vc or not vc.is_playing():
        await interaction.response.send_message("⚠️ 沒有正在播放的歌曲")
        return

    vc.stop()
    await interaction.response.send_message("⏭️ 已跳過目前歌曲")

# === /create_playlist 指令 ===
@bot.tree.command(name="create_playlist", description="建立新的歌單")
@app_commands.describe(name="歌單名稱")
async def create_playlist(interaction: discord.Interaction, name: str):
    user_id = str(interaction.user.id)
    playlists = load_playlist(user_id)

    if name in playlists:
        await interaction.response.send_message(f"⚠️ 歌單 `{name}` 已存在")
        return

    playlists[name] = []
    save_playlist(user_id, playlists)
    await interaction.response.send_message(f"✅ 已建立歌單 `{name}`")

# === /add_to_playlist 指令 ===
@bot.tree.command(name="add_to_playlist", description="將歌曲加入指定歌單")
@app_commands.describe(playlist="歌單名稱", title="歌曲標題")
@app_commands.autocomplete(playlist=playlist_name_autocomplete, title=song_title_autocomplete)
async def add_to_playlist(interaction: discord.Interaction, playlist: str, title: str):
    user_id = str(interaction.user.id)
    playlists = load_playlist(user_id)

    if playlist not in playlists:
        await interaction.response.send_message(f"⚠️ 找不到歌單 `{playlist}`")
        return

    song = find_song_by_title(title)
    if not song:
        await interaction.response.send_message("❌ 找不到此歌曲")
        return

    if song["id"] in playlists[playlist]:
        await interaction.response.send_message("⚠️ 此歌曲已在歌單中")
        return

    playlists[playlist].append(song["id"])
    save_playlist(user_id, playlists)
    await interaction.response.send_message(f"✅ 已加入 `{title}` 至 `{playlist}`")


# === /play_playlist 指令 ===
@bot.tree.command(name="play_playlist", description="播放指定歌單內的所有歌曲")
@app_commands.describe(name="你的歌單名稱")
@app_commands.autocomplete(name=playlist_name_autocomplete)
async def play_playlist(interaction: discord.Interaction, name: str):
    await interaction.response.defer()  # ✅ 防止超時

    user_id = str(interaction.user.id)
    logger.info(f"/play_playlist 執行者：{user_id}，目標歌單：{name}")
    playlists = load_playlist(user_id)

    # ✅ 載入 global_playlists.json 並合併
    try:
        global_playlists = load_playlist("global_playlists")
        playlists.update(global_playlists)
    except Exception as e:
        logger.warning(f"⚠️ 無法載入 global_playlists.json：{e}")

    if name not in playlists:
        await interaction.followup.send(f"⚠️ 找不到歌單 `{name}`", ephemeral=True)
        logger.warning("❌ 無法找到指定歌單")
        return

    song_ids = playlists[name]
    if not song_ids:
        await interaction.followup.send(f"📭 歌單 `{name}` 是空的", ephemeral=True)
        logger.info("🔎 歌單為空")
        return

    songs_data = load_songs()
    id_to_song = {int(song["id"]): song for song in songs_data}  # ✅ id 保證為 int
    songs = [id_to_song[sid] for sid in song_ids if sid in id_to_song]

    if not songs:
        await interaction.followup.send("⚠️ 歌單中沒有可播放的歌曲", ephemeral=True)
        return

    # 將歌曲加入佇列
    guild_id = interaction.guild.id
    guild_queues[guild_id].extend(songs)
    await interaction.followup.send(f"✅ 已加入 `{name}` 歌單至播放佇列！")
    logger.info(f"✅ 已加入佇列 {len(songs)} 首：{[s['title'] for s in songs]}")

    # 如果沒有正在播放，就立即開始
    if not playing_lock[guild_id]:
        playing_lock[guild_id] = True
        await play_from_queue(interaction.guild, interaction.user.voice.channel)





# === 啟動事件 ===
@bot.event
async def on_ready():
    print(f"✅ 登入成功：{bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Slash 指令同步成功（{len(synced)} 個）")
    except Exception as e:
        print(f"❌ 指令同步失敗：{e}")

print("🎯 準備連線 Discord")
bot.run(BOT_TOKEN)
