import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
import yt_dlp

# ===================== НАЛАШТУВАННЯ =====================
BASE_DIR = Path(r"C:\tiktok_bot")
RAW_DIR = BASE_DIR / "raw"
CHANNELS_FILE = BASE_DIR / "channels.txt"
HISTORY_FILE = BASE_DIR / "history.json"

MAX_VIDEOS_PER_RUN = 1       # скільки нових відео качати за один запуск
MIN_DURATION_SEC = 300       # ігнорувати відео коротші за 5 хвилин
MAX_DURATION_SEC = 3 * 3600  # ігнорувати відео довші за 3 години
MAX_HEIGHT = 1080            # максимальна якість (1080p, щоб не качати гігабайти)
# ========================================================

RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}
HEADERS = {"User-Agent": "Mozilla/5.0"}


def prepare_folders():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if not CHANNELS_FILE.exists():
        CHANNELS_FILE.write_text("# Вставте сюди посилання на канали\n", encoding="utf-8")


def load_history():
    if HISTORY_FILE.exists():
        try:
            return set(json.loads(HISTORY_FILE.read_text(encoding="utf-8")))
        except Exception:
            print("Файл history.json пошкоджений, починаю з чистого.")
    return set()


def save_history(history):
    HISTORY_FILE.write_text(
        json.dumps(sorted(history), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_channels():
    if not CHANNELS_FILE.exists():
        return []
    lines = CHANNELS_FILE.read_text(encoding="utf-8-sig").splitlines()
    result = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            result.append(line)
    return result


def resolve_channel_id(line):
    """Перетворює посилання або @назву каналу на ID виду UCxxxx."""
    if re.fullmatch(r"UC[\w-]{22}", line):
        return line
    match = re.search(r"/channel/(UC[\w-]{22})", line)
    if match:
        return match.group(1)

    if line.startswith("@"):
        line = "https://www.youtube.com/" + line
    if not line.startswith("http"):
        return None

    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "playlist_items": "1",
        "skip_download": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(line.rstrip("/") + "/videos", download=False)
        channel_id = info.get("channel_id") or info.get("uploader_id")
        if channel_id and str(channel_id).startswith("UC"):
            return channel_id
    except Exception as error:
        print(f"  Не вдалося визначити ID каналу: {error}")
    return None


def get_feed_videos(channel_id):
    """Читає безкоштовну RSS-стрічку і повертає список (id відео, назва)."""
    response = requests.get(RSS_URL.format(channel_id), headers=HEADERS, timeout=30)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    videos = []
    for entry in root.findall("atom:entry", NS):
        video_id = entry.find("yt:videoId", NS).text
        title = entry.find("atom:title", NS).text
        videos.append((video_id, title))
    return videos


def get_video_info(video_id):
    url = f"https://www.youtube.com/watch?v={video_id}"
    options = {"quiet": True, "no_warnings": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)


def download_video(video_id):
    url = f"https://www.youtube.com/watch?v={video_id}"
    video_format = (
        f"bv*[vcodec^=avc1][height<={MAX_HEIGHT}]+ba[ext=m4a]"
        f"/bv*[height<={MAX_HEIGHT}]+ba"
        f"/b[height<={MAX_HEIGHT}]"
    )
    options = {
        "format": video_format,
        "merge_output_format": "mp4",
        "outtmpl": str(RAW_DIR / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "windowsfilenames": True,
        "quiet": False,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([url])
    result = RAW_DIR / f"{video_id}.mp4"
    return result if result.exists() else None


def run():
    prepare_folders()
    history = load_history()
    channels = read_channels()

    if not channels:
        print("Файл channels.txt порожній. Додайте туди канали і запустіть знову.")
        return 0

    downloaded = 0
    for line in channels:
        if downloaded >= MAX_VIDEOS_PER_RUN:
            break

        print(f"\nКанал: {line}")
        channel_id = resolve_channel_id(line)
        if not channel_id:
            print("  Пропускаю: не вдалося знайти ID каналу. Перевірте посилання.")
            continue

        try:
            videos = get_feed_videos(channel_id)
        except Exception as error:
            print(f"  Не вдалося прочитати RSS-стрічку: {error}")
            continue

        new_videos = [v for v in videos if v[0] not in history]
        print(f"  Нових відео в стрічці: {len(new_videos)}")

        for video_id, title in new_videos:
            if downloaded >= MAX_VIDEOS_PER_RUN:
                break

            print(f"  Перевіряю: {title}")
            try:
                info = get_video_info(video_id)
            except Exception as error:
                print(f"    Не вдалося отримати дані про відео: {error}")
                continue

            duration = info.get("duration") or 0
            if info.get("is_live") or info.get("live_status") in ("is_upcoming", "is_live"):
                print("    Пропускаю: це трансляція (спробую пізніше).")
                continue
            if duration < MIN_DURATION_SEC or duration > MAX_DURATION_SEC:
                print(f"    Пропускаю: тривалість {duration // 60} хв не підходить.")
                history.add(video_id)
                save_history(history)
                continue

            print(f"    Качаю ({duration // 60} хв)...")
            try:
                path = download_video(video_id)
            except Exception as error:
                print(f"    Помилка завантаження: {error}")
                continue

            if path:
                history.add(video_id)
                save_history(history)
                downloaded += 1
                print(f"    Готово: {path}")
            else:
                print("    Файл не з'явився, щось пішло не так.")

    print(f"\nПідсумок: завантажено нових відео: {downloaded}")
    return downloaded


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\nЗупинено вручну.")
        sys.exit(0)