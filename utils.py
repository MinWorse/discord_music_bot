import os
import json
import logging
import asyncio
import aiohttp
from discord import FFmpegPCMAudio

# ✅ 載入 R2 URL
R2_SONGS_JSON_URL = os.getenv("R2_PUBLIC_BASE") + "/songs.json"
# ✅ 暫存歌曲資料
songs_cache = None
_main_loop = None

def init_utils(loop):
    global _main_loop
    _main_loop = loop
    logging.info(f"✅ [init_utils] 已設置主 event loop {loop}")

async def load_songs():
    """從 R2 載入 songs.json 並快取"""
    global songs_cache
    logging.info("🌐 正在從 R2 載入 songs.json ...")
    async with aiohttp.ClientSession() as session:
        async with session.get(R2_SONGS_JSON_URL) as resp:
            text = await resp.text()
            songs_cache = json.loads(text)
            logging.info(f"✅ songs.json 載入成功，共 {len(songs_cache)} 首")

def get_songs_cache():
    global songs_cache
    return songs_cache

def get_song_info_by_id(song_id: int):
    cache = get_songs_cache()
    if cache is None:
        logging.warning("⚠️ [get_song_info_by_id] songs_cache 尚未初始化")
        return None
    return next((song for song in cache if song.get("id") == song_id), None)

class GuildState:
    def __init__(self):
        self.queue = []
        self.is_playing = False
        self.vc = None

    async def start_playing(self, guild, text_channel, voice_channel):
        if self.is_playing or not self.queue:
            return
        self.is_playing = True
        song_id = self.queue[0]
        song_info = get_song_info_by_id(song_id)
        if not song_info:
            await text_channel.send(f"❌ 找不到歌曲 ID：{song_id}")
            self.queue.pop(0)
            self.is_playing = False
            await self.start_playing(guild, text_channel, voice_channel)
            return

        url = os.getenv("R2_PUBLIC_BASE") + f"/songs/{song_id}.mp3"
        logging.info(f"🔊 正在播放：{song_info['title']} - {song_info['artist']} (url={song_info['url']}) [id={song_id}]")
        try:
            if not self.vc or not self.vc.is_connected():
                self.vc = await voice_channel.connect()
            self.vc.play(
                FFmpegPCMAudio(url),
                after=after_callback_factory(guild, text_channel)
            )
        except Exception as e:
            logging.exception("❌ 播放失敗：", exc_info=e)
            self.queue.pop(0)
            self.is_playing = False
            await self.start_playing(guild, text_channel, voice_channel)

guild_states = {}

def get_guild_state(guild):
    if guild.id not in guild_states:
        guild_states[guild.id] = GuildState()
    return guild_states[guild.id]

async def handle_after_play(guild, text_channel, error):
    guild_id = guild.id
    state = get_guild_state(guild)
    if error:
        logging.error("🎵 播放出錯：", exc_info=error)
        await text_channel.send("⚠️ 播放發生錯誤，已跳過此曲")
    if state.queue:
        state.queue.pop(0)
    state.is_playing = False
    logging.info(f"✅ 播放完成")
    await state.start_playing(guild, text_channel, state.vc.channel if state.vc else None)
    if not state.queue:
        logging.info(f"🎵 檢查播放條件：queue=[], flag=False, guild_id={guild_id}")
        if state.vc and state.vc.is_connected():
            await state.vc.disconnect(force=True)
            state.vc = None
            await text_channel.send("📤 無歌曲播放，自動離開語音（已清空佇列）")

def after_callback_factory(guild, channel):
    def callback(error):
        try:
            global _main_loop
            loop = _main_loop or asyncio.get_event_loop()
            future = asyncio.run_coroutine_threadsafe(
                handle_after_play(guild, channel, error), loop)
            future.result()
        except Exception as e:
            logging.error("after callback failed", exc_info=e)
    return callback

async def reload_songs():
    """手動刷新 songs_cache，從 R2 重新載入 songs.json"""
    global songs_cache
    logging.info("🔄 [reload] 開始手動刷新 songs_cache ...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(R2_SONGS_JSON_URL) as resp:
                text = await resp.text()
                songs_cache = json.loads(text)
                logging.info(f"✅ [reload] songs.json 重新載入成功，共 {len(songs_cache)} 首")
    except Exception as e:
        logging.error("❌ [reload] songs.json 載入失敗", exc_info=e)
        raise