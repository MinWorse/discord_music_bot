import os
import json
import logging
import aiohttp
import asyncio
import time
from discord import FFmpegPCMAudio
from dotenv import load_dotenv
load_dotenv()

R2_SONGS_JSON_URL = os.getenv("R2_PUBLIC_BASE") + "/songs.json"

songs_cache = None
main_loop = None  # ⭐ 全域 event loop

def set_main_loop(loop):
    global main_loop
    main_loop = loop
    logging.info(f"✅ [init_utils] 已設置主 event loop {main_loop}")

async def load_songs():
    """從 R2 載入 songs.json 並快取"""
    global songs_cache
    logging.info("🌐 正在從 R2 載入 songs.json ...")
    async with aiohttp.ClientSession() as session:
        async with session.get(R2_SONGS_JSON_URL) as resp:
            text = await resp.text()
            songs_cache = json.loads(text)
            logging.info(f"✅ songs.json 載入成功，共 {len(songs_cache)} 首")

async def reload_songs():
    """重新載入 songs.json（用於 /reload 指令）"""
    await load_songs()
    logging.info(f"🔄 [reload] 歌曲清單重新載入，共 {len(songs_cache)} 首")

def get_song_info_by_id(song_id: int):
    if songs_cache is None:
        logging.warning("⚠️ [get_song_info_by_id] songs_cache 尚未初始化，請先呼叫 await load_songs()")
        return None
    return next((song for song in songs_cache if song.get("id") == song_id), None)

class GuildState:
    def __init__(self):
        self.queue = []
        self.is_playing = False
        self.vc = None
        self.current_mp3_seconds = None  # ⭐ 預期播放秒數
        self.start_time = None           # ⭐ 實際開始播放的時間

    async def start_playing(self, guild, text_channel, voice_channel):
        import utils
        if self.is_playing or not self.queue:
            return
        self.is_playing = True

        song_id = self.queue[0]
        song_info = utils.get_song_info_by_id(song_id)
        if not song_info:
            await text_channel.send(f"❌ 找不到歌曲 ID：{song_id}")
            self.queue.pop(0)
            self.is_playing = False
            await self.start_playing(guild, text_channel, voice_channel)
            return

        url = os.getenv("R2_PUBLIC_BASE") + f"/songs/{song_id}.mp3"
        # 檢查 mp3 時長
        self.current_mp3_seconds = await get_mp3_duration(url)
        self.start_time = None

        if self.current_mp3_seconds:
            logging.info(f"🕒 [mp3] 預期播放秒數：{self.current_mp3_seconds:.2f} 秒")
        logging.info(f"🔊 正在播放：{song_info['title']} - {song_info['artist']} (url={song_info['url']}) [id={song_id}]")

        try:
            if not self.vc or not self.vc.is_connected():
                self.vc = await voice_channel.connect()
            self.start_time = time.time()  # ⭐ 播放開始時刻
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

async def get_mp3_duration(url):
    """
    用 ffprobe 取得 mp3 長度（秒），需本機 ffprobe
    """
    import subprocess
    logging.info(f"🔎 [mp3] 嘗試取得 mp3 時長：{url}")
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode == 0 and out:
            return float(out.decode().strip())
    except Exception as e:
        logging.warning(f"⚠️ 取得 mp3 時長失敗：{e}")
    return None

async def handle_after_play(guild, text_channel, error):
    state = get_guild_state(guild)
    if error:
        logging.error("🎵 播放出錯：", exc_info=error)
        await text_channel.send("⚠️ 播放發生錯誤，已跳過此曲")
    # 播放時長 log
    if state.current_mp3_seconds is not None and state.start_time:
        real_time = time.time() - state.start_time
        logging.info(f"🕒 [mp3] 播放結束，預期長度：{state.current_mp3_seconds:.2f} 秒，實際耗時：約 {real_time:.2f} 秒")
    if state.queue:
        state.queue.pop(0)
    state.is_playing = False
    await state.start_playing(guild, text_channel, state.vc.channel if state.vc else None)
    if not state.queue:
        logging.info(f"🎵 檢查播放條件：queue=[], flag=False, guild_id={guild.id}")
        if state.vc and state.vc.is_connected():
            await state.vc.disconnect(force=True)
            state.vc = None
            await text_channel.send("📤 無歌曲播放，自動離開語音（已清空佇列）")
    state.current_mp3_seconds = None
    state.start_time = None

def after_callback_factory(guild, channel):
    def callback(error):
        try:
            future = asyncio.run_coroutine_threadsafe(
                handle_after_play(guild, channel, error),
                main_loop,
            )
            future.result()
        except Exception as e:
            logging.error("after callback failed", exc_info=e)
    return callback
