import logging
from discord import app_commands
from utils import get_songs_cache, load_songs

async def play_autocomplete(interaction, current: str):
    # songs_cache 若尚未載入自動補救（僅一次）
    cache = get_songs_cache()
    if cache is None:
        logging.warning("⚠️ [autocomplete] songs_cache 尚未初始化")
        await load_songs()
        cache = get_songs_cache()
        if cache is None:
            logging.error("❌ [autocomplete] songs_cache 還是 None")
            return [
                app_commands.Choice(name="⚠️ 歌曲清單尚未載入", value=-1)
            ]
    current_lower = current.strip().lower()
    matches = []
    for song in cache:
        if not current_lower or \
           current_lower in song['title'].lower() or \
           current_lower in song['artist'].lower():
            matches.append(song)
    logging.info(f"🔍 autocomplete 匹配到 {len(matches)} 首（current='{current}'）")
    matches.sort(key=lambda x: x['id'])
    limited = matches[:25]
    logging.info(f"🔍 autocomplete 顯示 {len(limited)} 首（最多25首）")
    return [
        app_commands.Choice(
            name=f"{song['title']} - {song['artist']}",
            value=song['id']
        ) for song in limited
    ]