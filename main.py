import os
import logging
import asyncio
import discord

from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
load_dotenv()

import utils  # 只 import utils

from autocomplete import play_autocomplete

TOKEN = os.getenv("DISCORD_TOKEN")
INTENTS = discord.Intents.default()
INTENTS.message_content = False

bot = commands.Bot(command_prefix="!", intents=INTENTS)

@bot.event
async def on_ready():
    utils.set_main_loop(asyncio.get_event_loop())
    await utils.load_songs()
    logging.info("✅ 登入成功：%s", bot.user)
    logging.info("🚩 on_ready: songs_cache 載入結果 type=%s, count=%s", type(utils.songs_cache), len(utils.songs_cache or []))
    try:
        synced = await bot.tree.sync()
        logging.info("✅ Slash 指令同步成功")
    except Exception as e:
        logging.exception("❌ 指令同步失敗：%s", e)

@bot.tree.command(name="play")
@app_commands.describe(song="請選擇歌曲")
@app_commands.autocomplete(song=play_autocomplete)
async def play(interaction: discord.Interaction, song: int):
    guild_id = interaction.guild_id
    user_id = interaction.user.id
    logging.info(f"📝 使用者輸入 /play {song}（guild_id={guild_id}, user_id={user_id}）")

    await interaction.response.defer()

    voice = interaction.user.voice
    if not voice or not voice.channel:
        await interaction.followup.send("⚠️ 請先加入語音頻道！")
        return

    songinfo = utils.get_song_info_by_id(song)
    if songinfo is None:
        await interaction.followup.send("❌ 查無此歌曲編號！")
        return

    state = utils.get_guild_state(interaction.guild)
    state.queue.append(song)
    logging.info(f"➕ 加入歌曲至佇列：{songinfo['title']}（guild_id={guild_id}）")
    await interaction.followup.send(f"✅ 已加入播放佇列：{songinfo['title']}。")

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
        state = utils.get_guild_state(interaction.guild)
        logging.info(f"🔧 [disconnect] 原始佇列長度：{len(state.queue)}，是否有 vc：{state.vc is not None}")
        state.queue.clear()
        state.is_playing = False
        logging.info(f"🔧 [disconnect] 已清空佇列與播放狀態")
        if state.vc:
            logging.info(f"🔧 [disconnect] 正在呼叫 vc.disconnect()...")
            await state.vc.disconnect(force=True)
            state.vc = None
        logging.info(f"✅ [disconnect] 語音斷線成功")
        await interaction.channel.send("播放資源已釋放完畢，可再次使用 `/play` 播放新歌曲。")
        logging.info(f"✅ [disconnect] 背景處理結束，guild_id={guild_id}")

    bot.loop.create_task(cleanup())

@bot.tree.command(name="stop")
async def stop(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    user_id = interaction.user.id
    logging.info(f"📝 使用者輸入 /stop（guild_id={guild_id}, user_id={user_id}）")
    state = utils.get_guild_state(interaction.guild)
    if state.vc and state.vc.is_playing():
        state.queue.clear()
        state.is_playing = False
        state.vc.stop()
        logging.info(f"⏹️ stop: queue cleared, state.is_playing=False, voice stopped.")
        await interaction.response.send_message("⏹️ 播放已停止。機器人仍在語音中，可繼續播放下一首。")
    else:
        await interaction.response.send_message("⚠️ 沒有播放中的歌曲")

@bot.tree.command(name="skip")
async def skip(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    user_id = interaction.user.id
    logging.info(f"📝 使用者輸入 /skip（guild_id={guild_id}, user_id={user_id}）")
    state = utils.get_guild_state(interaction.guild)
    if state.vc and state.vc.is_playing():
        state.vc.stop()
        logging.info(f"⏭️ skip: voice stopped, next song (if any) will start.")
        await interaction.response.send_message("⏭️ 用戶手動跳過歌曲")
    else:
        await interaction.response.send_message("⚠️ 沒有播放中的歌曲")

@bot.tree.command(name="reload")
async def reload(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    user_id = interaction.user.id
    logging.info(f"📝 使用者輸入 /reload（guild_id={guild_id}, user_id={user_id}）")
    await interaction.response.defer()
    try:
        await utils.reload_songs()
        await interaction.followup.send("✅ 歌曲清單已重新載入，autocomplete 和 /play 皆會用最新清單！")
    except Exception as e:
        await interaction.followup.send("❌ 重新載入失敗，請檢查 logs")
        logging.error("❌ reload_songs 失敗", exc_info=e)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')
    utils.set_main_loop(asyncio.get_event_loop())
    logging.info("🎯 準備連線 Discord")
    bot.run(TOKEN)
