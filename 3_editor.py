import json
import subprocess
import sys
from pathlib import Path

# ===================== НАЛАШТУВАННЯ =====================
BASE_DIR = Path(r"C:\tiktok_bot")
ANALYSIS_DIR = BASE_DIR / "analysis"
READY_DIR = BASE_DIR / "ready"
TEMP_DIR = BASE_DIR / "temp"

WIDTH, HEIGHT = 1080, 1920
SPEED = 1.02              # легке прискорення для унікальності
FONT_NAME = "Arial Black"
FONT_SIZE = 64            # розмір субтитрів
WORDS_PER_LINE = 3        # скільки слів показувати разом
MARGIN_V = 0
HIGHLIGHT_COLOR = "&H0000FFFF&"   # жовтий (формат BGR)

# --- Банер на хромакеї ---
BANNER_FILE = BASE_DIR / "banner.mp4"   # відео банера
BANNER_COLOR = "0x00B009"   # точний зелений вашого банера
BANNER_SIMILARITY = 0.12    # вузький діапазон, щоб не з'їсти сам банер
BANNER_BLEND = 0.05         # м'якість країв
BANNER_START = 15           # на якій секунді відео зупиняється і грає банер
BANNER_WIDTH = 900          # ширина банера в пікселях (екран 1080)
BANNER_Y = "(H-h)/2"        # положення: по центру; можна "150" (зверху) або "H-h-380" (знизу)
FPS = 30
# ========================================================


def ass_time(sec):
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def clean_word(word):
    word = word.replace("{", "").replace("}", "").replace("\\", "")
    return word.strip().upper()


def build_ass(words, clip_start, clip_end, ass_path):
    """Створює файл субтитрів: слова групами, поточне слово підсвічується."""
    clip_words = []
    for w in words:
        if w["start"] >= clip_start - 0.05 and w["end"] <= clip_end + 0.3:
            text = clean_word(w["word"])
            if text:
                clip_words.append({
                    "word": text,
                    "start": (w["start"] - clip_start) / SPEED,
                    "end": (w["end"] - clip_start) / SPEED,
                })

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{FONT_NAME},{FONT_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,5,60,60,{MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    groups = [clip_words[i:i + WORDS_PER_LINE] for i in range(0, len(clip_words), WORDS_PER_LINE)]
    for group in groups:
        for idx, current in enumerate(group):
            start = current["start"]
            if idx + 1 < len(group):
                end = group[idx + 1]["start"]
            else:
                end = current["end"] + 0.15
            if end <= start:
                end = start + 0.1
            parts = []
            for j, w in enumerate(group):
                if j == idx:
                    parts.append(f"{{\\c{HIGHLIGHT_COLOR}\\fscx112\\fscy112}}{w['word']}{{\\r}}")
                else:
                    parts.append(w["word"])
            text = " ".join(parts)
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Main,,0,0,0,,{text}")

    ass_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8-sig")


def ffmpeg_escape_path(path):
    """Екранує шлях для фільтра ffmpeg на Windows (C:\\x -> C\\:/x)."""
    p = str(path).replace("\\", "/")
    return p.replace(":", "\\:")


def get_duration(path, default):
    """Тривалість файлу в секундах через ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        return float(r.stdout.strip())
    except Exception:
        return default


def build_filter(ass_path, banner_len):
    """banner_len = 0 -> без банера. Інакше на BANNER_START відео зупиняється,
    грає банер (зі своїм звуком), потім відео продовжується."""
    ass = ffmpeg_escape_path(ass_path)
    base = (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},boxblur=30:5,eq=brightness=-0.08[bgb];"
        f"[fg]scale={WIDTH}:-2[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,"
        f"eq=contrast=1.03:brightness=0.01:saturation=1.04,"
        f"setpts=PTS/{SPEED},fps={FPS},format=yuv420p"
    )
    if not banner_len:
        return (
            base + f"[b0];[b0]ass='{ass}'[v];"
            f"[0:a]atempo={SPEED}[a]"
        )

    T = BANNER_START
    D = banner_len
    g = base + "[b0];"
    g += "[b0]split=2[bm][bf];"
    # основне відео з субтитрами, ріжемо на дві частини: до і після банера
    g += f"[bm]ass='{ass}',split=2[s1][s2];"
    g += f"[s1]trim=end={T},setpts=PTS-STARTPTS[vA];"
    g += f"[s2]trim=start={T},setpts=PTS-STARTPTS[vC];"
    # зупинений кадр (без субтитрів) на час банера + сам банер поверх
    g += (
        f"[bf]trim=start={T},setpts=PTS-STARTPTS,trim=end_frame=1,"
        f"tpad=stop_mode=clone:stop_duration={D},fps={FPS}[frz];"
        f"[1:v]scale={BANNER_WIDTH}:-2,fps={FPS},"
        f"chromakey={BANNER_COLOR}:{BANNER_SIMILARITY}:{BANNER_BLEND},"
        f"format=yuva420p,setpts=PTS-STARTPTS[ban];"
        f"[frz][ban]overlay=x=(W-w)/2:y={BANNER_Y}:eof_action=pass,"
        f"format=yuv420p,trim=duration={D},setpts=PTS-STARTPTS[vB];"
    )
    g += "[vA][vB][vC]concat=n=3:v=1:a=0[v];"
    # звук: основний до паузи, звук банера, основний після
    g += (
        f"[0:a]atempo={SPEED},aresample=48000,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo,asplit=2[m1][m2];"
        f"[m1]atrim=end={T},asetpts=PTS-STARTPTS[aA];"
        f"[m2]atrim=start={T},asetpts=PTS-STARTPTS[aC];"
        f"[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"apad=whole_dur={D},atrim=end={D},asetpts=PTS-STARTPTS[aB];"
        f"[aA][aB][aC]concat=n=3:v=0:a=1[a]"
    )
    return g


def run_ffmpeg(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.returncode == 0, result.stderr


def make_clip(video_path, clip, words, name):
    READY_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    ass_path = TEMP_DIR / f"{name}.ass"
    out_path = READY_DIR / f"{name}.mp4"
    build_ass(words, clip["start"], clip["end"], ass_path)

    duration = clip["end"] - clip["start"]
    out_len = duration / SPEED

    banner_len = 0
    if BANNER_FILE.exists():
        if out_len > BANNER_START + 3:
            banner_len = round(get_duration(BANNER_FILE, 5.5), 2)
        else:
            print("    Клип закороткий для банера, роблю без нього.")
    else:
        print("    Банер не знайдено, роблю без нього.")
    filter_graph = build_filter(ass_path, banner_len)

    base_cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{clip['start']:.2f}", "-t", f"{duration:.2f}",
        "-i", str(video_path),
    ]
    if banner_len:
        base_cmd += ["-i", str(BANNER_FILE)]
    base_cmd += [
        "-filter_complex", filter_graph,
        "-map", "[v]", "-map", "[a]",
        "-map_metadata", "-1", "-map_chapters", "-1",
        "-c:a", "aac", "-b:a", "160k",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
    ]

    gpu_cmd = base_cmd + ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21", "-b:v", "0", str(out_path)]
    ok, err = run_ffmpeg(gpu_cmd)
    if not ok:
        print("    Відеокарта не впоралась, пробую процесор...")
        cpu_cmd = base_cmd + ["-c:v", "libx264", "-preset", "fast", "-crf", "21", str(out_path)]
        ok, err = run_ffmpeg(cpu_cmd)
    if not ok:
        print(f"    ПОМИЛКА ffmpeg:\n{err[-1500:]}")
        return None

    title = clip.get("title", "")
    tags = " ".join(clip.get("hashtags", []))
    (READY_DIR / f"{name}.txt").write_text(f"{title}\n{tags}\n", encoding="utf-8")
    return out_path


def process(analysis_file):
    data = json.loads(analysis_file.read_text(encoding="utf-8"))
    video_path = Path(data["video_file"])
    if not video_path.exists():
        print(f"Відео не знайдено: {video_path}")
        return 0

    clips = data.get("clips", [])
    words = data.get("words", [])
    print(f"Відео: {video_path.name}, клипів до монтажу: {len(clips)}")

    made = 0
    for i, clip in enumerate(clips, 1):
        name = f"{data['video_id']}_{i}"
        out = READY_DIR / f"{name}.mp4"
        if out.exists():
            print(f"  Клип {i}: вже готовий, пропускаю.")
            continue
        print(f"  Клип {i} з {len(clips)}: {clip['title']}")
        if make_clip(video_path, clip, words, name):
            made += 1
            print(f"    Готово: {out}")

    (ANALYSIS_DIR / f"{data['video_id']}.done").write_text("ok", encoding="utf-8")
    print(f"\nПідсумок: створено клипів: {made}. Папка: {READY_DIR}")
    return made


def pending_analyses():
    files = sorted(ANALYSIS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
    return [f for f in files if not f.with_suffix(".done").exists()]


if __name__ == "__main__":
    try:
        queue = pending_analyses()
        if not queue:
            print("Немає нових результатів аналізу для монтажу.")
            sys.exit(0)
        process(queue[0])
    except KeyboardInterrupt:
        print("\nЗупинено вручну.")
        sys.exit(0)