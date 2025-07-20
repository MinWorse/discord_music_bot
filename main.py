import os
import logging
import asyncio
import discord

from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
load_dotenv()

from utils import get_song_info_by_id, get_guild_state, handle_after_play, load_songs

TOKEN = os.getenv("DISCORD_TOKEN")
INTENTS = discord.Intents.default()
INTENTS.message_content = False

bot = commands.Bot(command_prefix="!", intents=INTENTS)

@bot.event
async def on_ready():
    logging.info("✅ 登入成功：%s", bot.user)

    await load_songs()
    try:
        synced = await bot.tree.sync()
        logging.info("✅ Slash 指令同步成功")
    except Exception as e:
        logging.exception("❌ 指令同步失敗：%s", e)

@bot.tree.command(name="play")
@app_commands.describe(id="歌曲編號")
async def play(interaction: discord.Interaction, id: int):
    guild_id = interaction.guild_id
    user_id = interaction.user.id
    logging.info(f"📝 使用者輸入 /play {id}（guild_id={guild_id}, user_id={user_id}）")

    await interaction.response.defer()

    voice = interaction.user.voice
    if not voice or not voice.channel:
        await interaction.followup.send("⚠️ 請先加入語音頻道！")
        return

    song = get_song_info_by_id(id)
    if song is None:
        await interaction.followup.send("❌ 查無此歌曲編號！")
        return

    state = get_guild_state(interaction.guild, bot.loop)
    state.queue.append(id)
    logging.info(f"➕ 加入歌曲至佇列：{song['title']}（guild_id={guild_id}）")
    await interaction.followup.send(f"✅ 已加入播放佇列：{song['title']}。")

    if not state.is_playing:
        await state.start_playing(interaction.guild, interaction.channel, voice.channel)

@bot.tree.command(name="disconnect")
async def disconnect(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    user_id = interaction.user.id
    logging.info(f"📝 使用者輸入 /disconnect（guild_id={guild_id}, user_id={user_id}）")
    await interaction.response.send_message("📴 已中斷連線，請稍候清除播放資源...")

    async def cleanup():
        logging.info(f"🔧 [disconnect] 背景處理開始（guild_id={guild_id}）")
        state = get_guild_state(interaction.guild)
        logging.info(f"🔧 [disconnect] 原始佇列長度：{len(state.queue)}，是否有 vc：{state.vc is not None}")

        state.queue.clear()
        state.is_playing = False
        logging.info(f"🔧 [disconnect] 已清空佇列與播放狀態")

        if state.vc:
            logging.info(f"🔧 [disconnect] 正在呼叫 vc.disconnect()...")
            await state.vc.disconnect(force=True)
        logging.info(f"✅ [disconnect] 語音斷線成功")
        await interaction.channel.send("播放資源已釋放完畢，可再次使用 `/play` 播放新歌曲。")
        logging.info(f"✅ [disconnect] 背景處理結束，guild_id={guild_id}")

    bot.loop.create_task(cleanup())

@bot.tree.command(name="stop")
async def stop(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    user_id = interaction.user.id
    logging.info(f"📝 使用者輸入 /stop（guild_id={guild_id}, user_id={user_id}）")

    state = get_guild_state(interaction.guild)

    if state.vc and state.vc.is_playing():
        state.queue.clear()
        state.is_playing = False
        state.vc.stop()
        await interaction.response.send_message("⏹️ 播放已停止。機器人仍在語音中，可繼續播放下一首。")
    else:
        await interaction.response.send_message("⚠️ 沒有播放中的歌曲")

@bot.tree.command(name="skip")
async def skip(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    user_id = interaction.user.id
    logging.info(f"📝 使用者輸入 /skip（guild_id={guild_id}, user_id={user_id}）")

    state = get_guild_state(interaction.guild)
    if state.vc and state.vc.is_playing():
        state.vc.stop()
        await interaction.response.send_message("⏭️ 用戶手動跳過歌曲")
    else:
        await interaction.response.send_message("⚠️ 沒有播放中的歌曲")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')
    logging.info("🎯 準備連線 Discord")
    bot.run(TOKEN)