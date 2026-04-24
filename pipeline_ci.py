"""
Instagram Bike Reels Pipeline — GitHub Actions CI
- Merges 3 random bike videos from the same Drive folder as the YT pipeline
- Music: searches YouTube Shorts for trending bike reels (2026), picks random from top 5,
         extracts audio — no static library, fresh each run
- Audio/video length: cuts to whichever is shorter (capped at 59s)
- Posts to Instagram as a Reel via Graph API
- Full checkpoint/resume on crash
"""

import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

import drive_manager as drive
import checkpoint as ckpt
import cleanup
import sheets_manager as sheets

# ─── CONFIG ───────────────────────────────────────────────────────────────────
BASE_DIR   = Path(".")
REELS_DIR  = BASE_DIR / "reels"
MUSIC_DIR  = BASE_DIR / "music"
OUTPUT_DIR = BASE_DIR / "output"
TEMP_DIR   = OUTPUT_DIR / "temp"

VIDEOS_PER_REEL  = 3
POSTS_PER_DAY    = 6
MUSIC_VOLUME     = 0.85
MAX_DURATION     = 59
TARGET_W, TARGET_H = 1080, 1920

TG_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
FORCE_RUN  = os.environ.get("FORCE_RUN", "false").lower() == "true"

MUSIC_SUBFOLDER = "music"

# Curated song library — pipeline prefers Drive MP3s whose filenames contain these titles.
# Falls back to any MP3 in the Drive music/ folder if none match.
SONG_LIBRARY = [
    {"title": "Tokyo Drift",           "artist": "Teriyaki Boyz",              "vibe": "🏍️ Classic"},
    {"title": "Titanium",              "artist": "David Guetta ft. Sia",        "vibe": "🔥 Power"},
    {"title": "Believer",              "artist": "Imagine Dragons",             "vibe": "🔥 Energy"},
    {"title": "Bones",                 "artist": "Imagine Dragons",             "vibe": "🔥 Aggressive"},
    {"title": "Thunder",               "artist": "Imagine Dragons",             "vibe": "🔥 Energy"},
    {"title": "Starboy",               "artist": "The Weeknd",                  "vibe": "😎 Swag"},
    {"title": "Blinding Lights",       "artist": "The Weeknd",                  "vibe": "😎 Swag"},
    {"title": "Power",                 "artist": "Kanye West",                  "vibe": "😎 Attitude"},
    {"title": "God's Plan",            "artist": "Drake",                       "vibe": "😎 Attitude"},
    {"title": "Espresso",              "artist": "Sabrina Carpenter",           "vibe": "😎 Trendy"},
    {"title": "Thunderstruck",         "artist": "AC/DC",                       "vibe": "🎸 Classic Rock"},
    {"title": "Highway to Hell",       "artist": "AC/DC",                       "vibe": "🎸 Classic Rock"},
    {"title": "Ride",                  "artist": "Twenty One Pilots",           "vibe": "🌅 Cinematic"},
    {"title": "Levels",                "artist": "Avicii",                      "vibe": "⚡ EDM"},
    {"title": "Ghost",                 "artist": "Justin Bieber",               "vibe": "⚡ EDM"},
    {"title": "Lose Control",          "artist": "Teddy Swims",                 "vibe": "⚡ Vibe"},
    {"title": "Bad Guy",               "artist": "Billie Eilish",               "vibe": "😎 Dark"},
    {"title": "Superhero",             "artist": "Metro Boomin ft. Future",     "vibe": "🔥 Hard"},
    {"title": "Breaking Me",           "artist": "Topic ft. A7S",               "vibe": "⚡ EDM"},
    {"title": "Mockingbird",           "artist": "Eminem",                      "vibe": "😎 Deep"},
    {"title": "Lose Yourself",         "artist": "Eminem",                      "vibe": "🔥 Hype"},
    {"title": "Radioactive",           "artist": "Imagine Dragons",             "vibe": "🔥 Epic"},
    {"title": "Warriors",              "artist": "Imagine Dragons",             "vibe": "🔥 Epic"},
    {"title": "On Top of the World",   "artist": "Imagine Dragons",             "vibe": "🌅 Feels"},
    {"title": "Uptown Funk",           "artist": "Bruno Mars",                  "vibe": "😎 Fun"},
    {"title": "Can't Hold Us",         "artist": "Macklemore & Ryan Lewis",     "vibe": "🔥 Hype"},
    {"title": "Hall of Fame",          "artist": "The Script ft. will.i.am",   "vibe": "🌅 Motivational"},
    {"title": "Whatever It Takes",     "artist": "Imagine Dragons",             "vibe": "🔥 Drive"},
    {"title": "Purple Rain",           "artist": "Prince",                      "vibe": "🌅 Cinematic"},
    {"title": "Titanium x Please Me",  "artist": "TRUE CHAD",                   "vibe": "⚡ Currently Viral"},
]

for d in [REELS_DIR, MUSIC_DIR, OUTPUT_DIR, TEMP_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─── TELEGRAM ─────────────────────────────────────────────────────────────────
def tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID:
        print(f"[TG] {msg}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception as e:
        print(f"[TG failed] {e}")

# ─── PROGRESS BAR ─────────────────────────────────────────────────────────────
def progress_bar(label, pct, width=35):
    filled = int(width * pct / 100)
    bar = "#" * filled + "-" * (width - filled)
    print(f"\r  [{bar}] {pct:5.1f}%  {label}", end="", flush=True)

def progress_done(label):
    print(f"\r  [{'#'*35}] 100.0%  {label} OK")

# ─── TRACKER ──────────────────────────────────────────────────────────────────
def load_tracker():
    if Path("ig_tracker.json").exists():
        with open("ig_tracker.json") as f:
            return json.load(f)
    return {"used_videos": [], "used_music": [], "posts_today": [], "total_posts": 0}

def save_tracker(data):
    with open("ig_tracker.json", "w") as f:
        json.dump(data, f, indent=2)

def get_available_videos(tracker):
    all_videos_file = Path("all_videos.json")
    if not all_videos_file.exists():
        print("  Building video index from Google Drive...")
        folder_id = os.environ.get("GDRIVE_FOLDER_ID", "1CivbmOzwqkCmyOZmmC7UC2p6EGDitjCB")
        service = drive.get_service()
        files = drive.list_folder(service, folder_id, name_filter=".mp4")
        all_videos = [f["name"] for f in files]
        with open(all_videos_file, "w") as f:
            json.dump(all_videos, f)
        print(f"  Found {len(all_videos)} videos in Drive")
    else:
        with open(all_videos_file) as f:
            all_videos = json.load(f)

    used = set(tracker.get("used_videos", []))
    available = [v for v in all_videos if v not in used]
    if len(available) < VIDEOS_PER_REEL:
        print("  All videos used — resetting tracker")
        tracker["used_videos"] = []
        available = all_videos
    return available

# ─── MUSIC SELECTION ──────────────────────────────────────────────────────────
def _library_meta(filename):
    """Return SONG_LIBRARY entry whose title appears in filename, or None."""
    name_lower = filename.lower()
    return next((s for s in SONG_LIBRARY if s["title"].lower() in name_lower), None)

def get_trending_music(tracker, force_track_id=None):
    """
    Pick a random MP3 from Drive's music/ subfolder.
    Prefers files whose names match a SONG_LIBRARY title.
    Falls back to any MP3 in the folder if none match.
    On checkpoint resume, re-downloads the exact same file by name.
    """
    service = drive.get_service()
    folder_id = os.environ.get("GDRIVE_FOLDER_ID", "1CivbmOzwqkCmyOZmmC7UC2p6EGDitjCB")
    music_folder_id = drive.find_or_create_folder(service, MUSIC_SUBFOLDER, folder_id)

    all_tracks = drive.list_folder(service, music_folder_id, name_filter=".mp3")
    if not all_tracks:
        raise RuntimeError("No MP3s found in Drive music/ folder. Upload some tracks first.")

    # Resume: re-download exact same track
    if force_track_id:
        match = next((t for t in all_tracks if t["name"] == force_track_id), None)
        if match:
            audio_file = MUSIC_DIR / force_track_id
            if not audio_file.exists():
                drive.download_file(service, match["id"], audio_file)
            meta = _library_meta(force_track_id)
            return {
                "id": force_track_id,
                "title": meta["title"] if meta else force_track_id.replace(".mp3", ""),
                "artist": meta["artist"] if meta else "Drive",
            }, audio_file

    recently_used = set(tracker.get("used_music", []))

    # Prefer curated library songs available on Drive
    curated = [t for t in all_tracks if _library_meta(t["name"])]
    if curated:
        pool_label = "curated"
        available = [t for t in curated if t["name"] not in recently_used] or curated
    else:
        # Fallback: any MP3 in the folder
        pool_label = "fallback"
        available = [t for t in all_tracks if t["name"] not in recently_used] or all_tracks

    chosen = random.choice(available)
    track_name = chosen["name"]
    audio_file = MUSIC_DIR / track_name

    if not audio_file.exists():
        print(f"  Downloading from Drive: {track_name}")
        drive.download_file(service, chosen["id"], audio_file)
    else:
        print(f"  Using cached: {track_name}")

    meta = _library_meta(track_name)
    title  = meta["title"]  if meta else track_name.replace(".mp3", "")
    artist = meta["artist"] if meta else "Drive"
    vibe   = meta["vibe"]   if meta else ""
    print(f"  Music [{pool_label}]: {title} — {artist} {vibe}")
    return {"id": track_name, "title": title, "artist": artist}, audio_file

# ─── VIDEO PROCESSING ─────────────────────────────────────────────────────────
def get_duration(media_path):
    result = subprocess.run([
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", str(media_path)
    ], capture_output=True, text=True)
    return float(json.loads(result.stdout)["format"]["duration"])

def run_ffmpeg_progress(cmd, label, total_dur):
    cmd = list(cmd) + ["-progress", "pipe:1", "-nostats"]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, bufsize=1
    )
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("out_time_ms="):
            try:
                ms = int(line.split("=")[1])
                pct = min(ms / 1_000_000 / total_dur * 100, 99.9)
                progress_bar(label, pct)
            except (ValueError, ZeroDivisionError):
                pass
        elif line == "progress=end":
            break
    proc.wait()
    if proc.returncode != 0:
        print()
        raise RuntimeError(f"ffmpeg failed: {label}")
    progress_done(label)

def normalize_clip(src_name, clip_index, dur):
    src = REELS_DIR / src_name
    dst = TEMP_DIR / f"norm_{clip_index}.mp4"
    run_ffmpeg_progress([
        "ffmpeg", "-y", "-i", str(src),
        "-vf", (
            f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
            f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps=30"
        ),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24", "-an",
        str(dst)
    ], f"Clip {clip_index+1}/3  {src_name[:25]}", dur)
    return dst

def merge_clips(normalized_files):
    concat_list = TEMP_DIR / "concat.txt"
    with open(concat_list, "w") as f:
        for n in normalized_files:
            abs_path = Path(n).resolve().as_posix()
            f.write(f"file '{abs_path}'\n")
    merged = TEMP_DIR / "merged.mp4"
    print("  Merging...", end="", flush=True)
    result = subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list), "-c", "copy", str(merged)
    ], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"\n  ffmpeg error: {result.stderr[-500:]}")
        raise RuntimeError("Merge failed")
    print(" OK")
    return merged

def find_best_audio_segment(audio_file, want_dur, chunk_sec=2):
    """
    Single ffmpeg pass: measure RMS energy every chunk_sec seconds.
    Returns the start time (seconds) of the most energetic want_dur-second window.
    Skips the first 10s (usually intro/silence) and last 10s (outro).
    """
    import tempfile
    stats_file = Path(tempfile.mktemp(suffix=".txt"))
    try:
        subprocess.run([
            "ffmpeg", "-i", str(audio_file),
            "-af", (
                f"astats=metadata=1:reset={chunk_sec},"
                f"ametadata=mode=print:file={stats_file}"
            ),
            "-f", "null", "-"
        ], capture_output=True, text=True)

        rms_values = []
        if stats_file.exists():
            for line in stats_file.read_text(errors="ignore").splitlines():
                if "lavfi.astats.Overall.RMS_level" in line:
                    try:
                        rms_values.append(float(line.split("=")[1].strip()))
                    except (ValueError, IndexError):
                        rms_values.append(-100.0)
    finally:
        stats_file.unlink(missing_ok=True)

    if not rms_values:
        return 0.0

    # Skip intro/outro (first/last 10s worth of chunks)
    skip = max(0, int(10 / chunk_sec))
    rms_values = rms_values[skip: max(skip + 1, len(rms_values) - skip)]

    window = max(1, round(want_dur / chunk_sec))
    best_i, best_score = 0, float("-inf")

    for i in range(len(rms_values) - window + 1):
        valid = [v for v in rms_values[i:i + window] if v > -80]
        if not valid:
            continue
        score = sum(valid) / len(valid)
        if score > best_score:
            best_score = score
            best_i = i

    return (best_i + skip) * chunk_sec  # add back skipped offset

def encode_final(merged_path, audio_file, video_duration, output_path):
    """Cut to whichever is shorter — video or audio — capped at MAX_DURATION."""
    audio_duration = get_duration(audio_file)
    final_dur = min(video_duration, audio_duration, MAX_DURATION)

    print(f"  Analyzing audio for best {final_dur:.0f}s segment...", flush=True)
    audio_start = find_best_audio_segment(audio_file, final_dur)
    audio_start = min(audio_start, max(0.0, audio_duration - final_dur))

    print(f"  Video: {video_duration:.1f}s  Audio: {audio_duration:.1f}s  "
          f"-> Final: {final_dur:.1f}s  (audio from {audio_start:.0f}s)")

    run_ffmpeg_progress([
        "ffmpeg", "-y",
        "-i", str(merged_path),
        "-ss", str(audio_start), "-i", str(audio_file),
        "-t", str(final_dur),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24",
        "-c:a", "aac", "-b:a", "192k",
        "-af", f"volume={MUSIC_VOLUME}",
        str(output_path)
    ], f"Encoding Reel ({final_dur:.0f}s, audio@{audio_start:.0f}s)", final_dur)
    return final_dur

# ─── INSTAGRAM UPLOAD ─────────────────────────────────────────────────────────
CAPTIONS = [
    "Bike life at its finest\n\n#BikeLife #Motorcycle #Reels #MotoVlog #BikeReels #Rider #TwoWheels",
    "Living for the ride\n\n#BikeLife #MotoLife #Reels #Rider #BikeLovers #Motorcycle #BikeReels",
    "Born to ride\n\n#BikeLife #Reels #Motorcycle #MotoVlog #BikeReels #TwoWheels #RideOrDie",
    "The road never lies\n\n#BikeLife #Rider #MotoLife #Reels #BikeLovers #Motorcycle #Viral",
    "Speed is life\n\n#BikeLife #Reels #MotoVlog #Motorcycle #BikeReels #Rider #TwoWheels",
    "Two wheels one soul\n\n#BikeLife #MotoLife #Reels #BikeReels #Motorcycle #Rider #Viral",
]

def upload_to_instagram(video_path):
    from instagrapi import Client

    username    = os.environ.get("IG_USERNAME", "")
    password    = os.environ.get("IG_PASSWORD", "")
    session_raw = os.environ.get("IG_SESSION_JSON", "")

    if not username or not password:
        raise RuntimeError("IG_USERNAME and IG_PASSWORD env vars required")

    cl = Client()

    # Load saved session to avoid triggering 2FA on every run
    session_file = Path("ig_session.json")
    if session_raw:
        session_file.write_text(session_raw)
    if session_file.exists():
        cl.load_settings(str(session_file))
        print("  Loaded saved IG session")

    cl.login(username, password)

    # Persist updated session to Drive state so it survives across runs
    cl.dump_settings(str(session_file))

    caption = random.choice(CAPTIONS)
    print(f"  Caption: {caption[:50]}...")
    print("  Uploading Reel...")

    media = cl.clip_upload(str(video_path), caption=caption)
    url = f"https://www.instagram.com/reel/{media.code}/"

    print(f"\n  Live: {url}")
    return str(media.pk), url

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def run():
    start = time.time()
    print("\n" + "="*52)
    print("  BIKE IG REELS PIPELINE (GitHub Actions CI)")
    print("="*52)

    tracker = load_tracker()
    today = datetime.now().strftime("%Y-%m-%d")
    posts_today = [p for p in tracker.get("posts_today", []) if p.startswith(today)]

    print(f"  Posts today  : {len(posts_today)}/{POSTS_PER_DAY}")
    print(f"  Total posted : {tracker.get('total_posts', 0)}")
    print(f"  Videos used  : {len(tracker.get('used_videos', []))}")

    if len(posts_today) >= POSTS_PER_DAY and not FORCE_RUN:
        msg = f"Daily limit reached ({POSTS_PER_DAY}/day). Skipping."
        print(f"\n  {msg}")
        tg(f"IG — Skipped: {msg}")
        return

    cp = ckpt.load()
    cp = cleanup.startup_check(cp)
    state = cp.get("state", "idle")
    print(f"  Checkpoint   : {state}")
    print("-"*52)

    # ── Step 1: Select videos + music ─────────────────────────────────────────
    if not ckpt.past(state, "videos_selected"):
        print("\n[1/4] Selecting videos & music...")
        try:
            from drive_manager import download_state
            download_state()
            tracker = load_tracker()
            posts_today = [p for p in tracker.get("posts_today", []) if p.startswith(today)]
            print(f"  Tracker refreshed — used: {len(tracker.get('used_videos', []))} videos")
        except Exception as e:
            print(f"  Tracker refresh failed (using cached): {e}")

        available = get_available_videos(tracker)
        random.shuffle(available)
        selected_videos = random.sample(available, VIDEOS_PER_REEL)
        print(f"  Videos: {[v[:20] for v in selected_videos]}")

        track_info, audio_file = get_trending_music(tracker)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(OUTPUT_DIR / f"reel_{timestamp}.mp4")
        ckpt.save("videos_selected",
                  selected_videos=selected_videos,
                  track_id=track_info["id"],
                  track_title=track_info["title"],
                  audio_file=str(audio_file),
                  output_file=output_path,
                  durations={})
        cp = ckpt.load()
        state = cp["state"]
        tg(f"IG Step 1/4 - Selected {len(selected_videos)} clips\nMusic: {track_info['title'][:50]}")
    else:
        print("\n[1/4] Already selected - skipping")
        selected_videos = cp["selected_videos"]
        audio_file = Path(cp["audio_file"])
        output_path = cp["output_file"]
        # Re-download audio if not on disk (new CI job)
        if not audio_file.exists():
            _, audio_file = get_trending_music(tracker, force_track_id=cp["track_id"])
        _meta = _library_meta(cp["track_id"])
        track_info = {
            "id": cp["track_id"],
            "title": cp.get("track_title", cp["track_id"]),
            "artist": _meta["artist"] if _meta else "Drive",
        }

    durations = cp.get("durations", {})

    # ── Step 2: Download + normalize clips ────────────────────────────────────
    print("\n[2/4] Downloading & normalizing clips...")
    tg("IG Step 2/4 - Downloading & normalizing clips...")
    normalized = []
    total_duration = 0.0

    for i, name in enumerate(selected_videos):
        clip_state = f"clip_{i}_done"
        norm_file = TEMP_DIR / f"norm_{i}.mp4"

        if ckpt.past(state, clip_state) and norm_file.exists():
            dur = durations.get(str(i), get_duration(REELS_DIR / name))
            print(f"  Clip {i+1}/3: {name[:25]} - already done, skipping")
        else:
            if not (REELS_DIR / name).exists():
                print(f"  Downloading from Drive: {name}")
                drive.download_videos([name])
            dur = get_duration(REELS_DIR / name)
            normalize_clip(name, i, dur)
            durations[str(i)] = dur
            ckpt.save(clip_state,
                      selected_videos=selected_videos,
                      track_id=cp["track_id"],
                      track_title=cp.get("track_title", ""),
                      audio_file=str(audio_file),
                      output_file=output_path,
                      durations=durations)
            cp = ckpt.load()
            state = cp["state"]

        normalized.append(str(norm_file))
        total_duration += dur

    # ── Step 3: Merge + Encode ─────────────────────────────────────────────────
    print("\n[3/4] Merging & encoding...")
    tg("IG Step 3/4 - Merging & encoding Reel...")
    merged_file = TEMP_DIR / "merged.mp4"

    if not (ckpt.past(state, "merged") and merged_file.exists()):
        merge_clips(normalized)
        ckpt.save("merged", selected_videos=selected_videos,
                  track_id=cp["track_id"], track_title=cp.get("track_title", ""),
                  audio_file=str(audio_file), output_file=output_path, durations=durations)
        state = "merged"

    output_file = Path(output_path)
    if not (ckpt.past(state, "encoded") and output_file.exists()):
        final_dur = encode_final(merged_file, audio_file, total_duration, output_file)
        ckpt.save("encoded", selected_videos=selected_videos,
                  track_id=cp["track_id"], track_title=cp.get("track_title", ""),
                  audio_file=str(audio_file), output_file=output_path, durations=durations)
        state = "encoded"
    else:
        final_dur = min(total_duration, MAX_DURATION)
        print("  Encode - already done, skipping")

    # ── Step 4: Upload ─────────────────────────────────────────────────────────
    print("\n[4/4] Uploading to Instagram...")
    tg("IG Step 4/4 - Uploading to Instagram...")

    if ckpt.past(state, "uploaded"):
        media_id = cp.get("media_id", "")
        url = cp.get("ig_url", f"https://www.instagram.com/reel/{media_id}/")
        print(f"  Already uploaded: {url}")
    else:
        media_id, url = upload_to_instagram(output_file)
        ckpt.save("uploaded", selected_videos=selected_videos,
                  track_id=cp["track_id"], track_title=cp.get("track_title", ""),
                  audio_file=str(audio_file), output_file=output_path,
                  durations=durations, media_id=media_id, ig_url=url)
        state = "uploaded"

    # ── Done ───────────────────────────────────────────────────────────────────
    tracker["used_videos"].extend(selected_videos)
    used_music = tracker.get("used_music", [])
    used_music.append(track_info["id"])
    tracker["used_music"] = used_music[-20:]
    tracker["posts_today"] = posts_today + [f"{today}_{datetime.now().strftime('%H%M%S')}"]
    tracker["total_posts"] = tracker.get("total_posts", 0) + 1
    save_tracker(tracker)
    ckpt.clear()
    cleanup.clean_temp()

    sheet_url = sheets.log_post(
        selected_videos=selected_videos,
        track_info=track_info,
        ig_url=url,
        post_number=tracker["total_posts"]
    )

    elapsed = time.time() - start
    summary = (
        f"*IG Reel posted!*\n"
        f"URL: {url}\n"
        f"Music: {track_info['title'][:50]}\n"
        f"Posts today: {len(posts_today)+1}/6\n"
        f"Total: {tracker['total_posts']}\n"
        f"Time: {elapsed:.0f}s"
    )
    if sheet_url:
        summary += f"\n[Sheet]({sheet_url})"
    tg(summary)
    print(f"\n{'='*52}")
    print(f"  Done in {elapsed:.0f}s | Total: {tracker['total_posts']}")
    print(f"{'='*52}\n")

if __name__ == "__main__":
    run()
