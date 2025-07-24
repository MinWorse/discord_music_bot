import logging
from discord import app_commands
import utils

# 預設每頁最多顯示 25 首歌（Discord 上限）
PAGE_SIZE = 25

async def play_autocomplete(interaction, current):
    # 這裡直接從 utils 取用 songs_cache
    if utils.songs_cache is None:
        logging.warning("⚠️ [autocomplete] songs_cache 尚未初始化")
        return [app_commands.Choice(name="⚠️ 歌曲清單尚未載入", value=-1)]

    current_lower = current.strip().lower()
    matches = []
    for song in utils.songs_cache:
        if not current_lower or \
           current_lower in song['title'].lower() or \
           current_lower in song['artist'].lower():
            matches.append(song)
    logging.info(f"🔍 autocomplete 匹配到 {len(matches)} 首（current='{current}'）")

    matches.sort(key=lambda x: x['id'])
    limited = matches[:PAGE_SIZE]
    logging.info(f"🔍 autocomplete 顯示 {len(limited)} 首（最多{PAGE_SIZE}首）")
    return [
        app_commands.Choice(
            name=f"{song['title']} - {song['artist']}",
            value=song['id']
        ) for song in limited
    ]
