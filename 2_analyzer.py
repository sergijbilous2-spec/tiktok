import json
import os
import re
import sys
from pathlib import Path

# Підключаємо файли для роботи з відеокартою (встановлені раніше через pip)
try:
    import nvidia.cublas
    import nvidia.cudnn
    for _pkg in (nvidia.cublas, nvidia.cudnn):
        _bin = Path(list(_pkg.__path__)[0]) / "bin"
        if _bin.exists():
            os.add_dll_directory(str(_bin))
            os.environ["PATH"] = str(_bin) + os.pathsep + os.environ["PATH"]
except Exception:
    pass

import ollama
from faster_whisper import WhisperModel

# ===================== НАЛАШТУВАННЯ =====================
BASE_DIR = Path(r"C:\tiktok_bot")
RAW_DIR = BASE_DIR / "raw"
ANALYSIS_DIR = BASE_DIR / "analysis"

WHISPER_MODEL = "large-v3-turbo"   # якщо буде не вистачати пам'яті, поставте "medium"
OLLAMA_MODEL = "qwen2.5:7b"

MIN_CLIP_SEC = 25
MAX_CLIP_SEC = 55
CHUNK_MINUTES = 12      # довге відео ділимо на шматки по 12 хвилин
CLIPS_PER_CHUNK = 2     # скільки моментів просимо в кожному шматку
MAX_CLIPS_TOTAL = 5     # максимум клипів з одного відео
# ========================================================

SYSTEM_PROMPT = (
    "Ти досвідчений редактор коротких вертикальних відео для TikTok. "
    "Ти вмієш знаходити найемоційніші, суперечливі та інсайтові моменти. "
    "Відповідай ТІЛЬКИ валідним JSON без жодного іншого тексту."
)


def format_time(seconds):
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def transcribe(video_path):
    """Розпізнає мову на відеокарті, повертає список слів з таймкодами і мову."""
    print(f"Завантажую модель розпізнавання ({WHISPER_MODEL}) на відеокарту...")
    model = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16")

    print("Розпізнаю мову, це може тривати кілька хвилин...")
    segments, info = model.transcribe(
        str(video_path),
        word_timestamps=True,
        vad_filter=True,
        beam_size=5,
    )
    print(f"Мова відео: {info.language}, тривалість: {format_time(info.duration)}")

    words = []
    sentences = []
    last_print = 0
    for seg in segments:
        sentences.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
        for w in (seg.words or []):
            words.append({"word": w.word.strip(), "start": w.start, "end": w.end})
        if seg.end - last_print > 120:
            print(f"  ...оброблено {format_time(seg.end)}")
            last_print = seg.end

    # звільняємо відеокарту для Ollama
    del model
    import gc
    gc.collect()
    return words, sentences, info.language


def chunk_sentences(sentences):
    """Ділить речення на шматки по CHUNK_MINUTES хвилин."""
    chunks = []
    current = []
    chunk_start = None
    limit = CHUNK_MINUTES * 60
    for s in sentences:
        if chunk_start is None:
            chunk_start = s["start"]
        current.append(s)
        if s["end"] - chunk_start >= limit:
            chunks.append(current)
            current = []
            chunk_start = None
    if current:
        chunks.append(current)
    return chunks


def build_prompt(chunk, language):
    lines = [f"[{s['start']:.1f}] {s['text']}" for s in chunk]
    transcript = "\n".join(lines)
    return f"""Нижче транскрипт відео. Кожен рядок починається з часу в секундах у квадратних дужках.

ЗАВДАННЯ: знайди {CLIPS_PER_CHUNK} найкращі моменти для TikTok-клипів.

ВИМОГИ ДО КОЖНОГО МОМЕНТУ:
- тривалість від {MIN_CLIP_SEC} до {MAX_CLIP_SEC} секунд (end мінус start);
- момент емоційний, суперечливий або дає цікавий інсайт;
- чіткий початок (повна думка, не середина речення) і чіткий кінець;
- start і end беруться з часових міток транскрипту.

Мова транскрипту: {language}. Заголовок і хештеги пиши ТІЄЮ Ж МОВОЮ.

ФОРМАТ ВІДПОВІДІ (строго JSON):
{{"clips": [{{"start": 123.4, "end": 160.0, "title": "Цепляючий заголовок", "hashtags": ["#тег1", "#тег2", "#тег3", "#тег4"], "score": 8}}]}}

score це оцінка віральності від 1 до 10.
Хештегів має бути 4-5.

ТРАНСКРИПТ:
{transcript}"""


def ask_llm(prompt):
    response = ollama.chat(
        model=OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        format="json",
        options={"temperature": 0.3, "num_ctx": 16384},
    )
    text = response["message"]["content"]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    clips = data.get("clips", []) if isinstance(data, dict) else []
    return clips if isinstance(clips, list) else []


def snap_to_sentences(start, end, sentences):
    """Підтягує межі клипу до початку/кінця найближчих речень."""
    new_start = start
    new_end = end
    for s in sentences:
        if s["start"] <= start <= s["end"] or abs(s["start"] - start) < 1.5:
            new_start = s["start"]
            break
    for s in sentences:
        if s["start"] <= end <= s["end"] or abs(s["end"] - end) < 1.5:
            new_end = s["end"]
            break
    return new_start, new_end


def validate_clip(clip, sentences, video_duration):
    try:
        start = float(clip["start"])
        end = float(clip["end"])
    except (KeyError, TypeError, ValueError):
        return None

    start, end = snap_to_sentences(start, end, sentences)
    start = max(0.0, start)
    end = min(end, video_duration)
    length = end - start

    if length < MIN_CLIP_SEC - 5 or length > MAX_CLIP_SEC + 8:
        return None

    title = str(clip.get("title", "")).strip() or "Цікавий момент"
    hashtags = clip.get("hashtags", [])
    if not isinstance(hashtags, list):
        hashtags = []
    cleaned = []
    for h in hashtags:
        h = str(h).strip().replace(" ", "")
        if not h:
            continue
        if not h.startswith("#"):
            h = "#" + h
        cleaned.append(h)
    try:
        score = float(clip.get("score", 5))
    except (TypeError, ValueError):
        score = 5.0

    return {
        "start": round(start, 2),
        "end": round(end, 2),
        "title": title,
        "hashtags": cleaned[:5],
        "score": score,
    }


def remove_overlaps(clips):
    clips = sorted(clips, key=lambda c: c["score"], reverse=True)
    result = []
    for c in clips:
        overlap = any(not (c["end"] <= r["start"] or c["start"] >= r["end"]) for r in result)
        if not overlap:
            result.append(c)
    return sorted(result, key=lambda c: c["start"])


def analyze(video_path):
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    video_id = video_path.stem
    out_file = ANALYSIS_DIR / f"{video_id}.json"

    words, sentences, language = transcribe(video_path)
    if not sentences:
        print("Мову не знайдено. Пропускаю це відео.")
        return None

    video_duration = sentences[-1]["end"]
    chunks = chunk_sentences(sentences)
    print(f"\nШукаю найцікавіші моменти (шматків для аналізу: {len(chunks)})...")

    all_clips = []
    for i, chunk in enumerate(chunks, 1):
        print(f"  Аналізую шматок {i} з {len(chunks)}...")
        prompt = build_prompt(chunk, language)
        for attempt in range(2):
            try:
                raw_clips = ask_llm(prompt)
                break
            except Exception as error:
                print(f"    Помилка звернення до Ollama: {error}")
                raw_clips = []
        for raw in raw_clips:
            clip = validate_clip(raw, sentences, video_duration)
            if clip:
                all_clips.append(clip)

    final_clips = remove_overlaps(all_clips)
    final_clips = sorted(final_clips, key=lambda c: c["score"], reverse=True)[:MAX_CLIPS_TOTAL]
    final_clips = sorted(final_clips, key=lambda c: c["start"])

    result = {
        "video_id": video_id,
        "video_file": str(video_path),
        "language": language,
        "clips": final_clips,
        "words": words,
    }
    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nЗнайдено клипів: {len(final_clips)}")
    for c in final_clips:
        print(f"  {format_time(c['start'])} - {format_time(c['end'])} "
              f"({c['end'] - c['start']:.0f} с) | оцінка {c['score']:.0f} | {c['title']}")
        print(f"    {' '.join(c['hashtags'])}")
    print(f"\nРезультат збережено: {out_file}")
    return out_file


def pending_videos():
    videos = sorted(RAW_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    return [v for v in videos if not (ANALYSIS_DIR / f"{v.stem}.json").exists()]


if __name__ == "__main__":
    try:
        queue = pending_videos()
        if not queue:
            print("У папці raw немає нових відео для аналізу.")
            sys.exit(0)
        analyze(queue[0])
    except KeyboardInterrupt:
        print("\nЗупинено вручну.")
        sys.exit(0)