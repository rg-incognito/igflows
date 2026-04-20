"""
Cleanup strategy:
- Temp folder: always wiped after successful run
- Output folder: keep last 12 Reels (2 days worth), delete older ones
- If disk space low (< 2GB free): delete more aggressively
- Broken temp files from a crashed run: detected and cleaned before resuming
"""

import shutil
import os
from pathlib import Path

BASE_DIR   = Path(os.environ.get("PIPELINE_BASE_DIR", "."))
OUTPUT_DIR = BASE_DIR / "output"
TEMP_DIR   = OUTPUT_DIR / "temp"

KEEP_LAST_N_OUTPUTS = 12
MIN_FREE_SPACE_GB = 2.0
LOW_SPACE_KEEP_N = 4

def get_free_space_gb():
    path = str(BASE_DIR.drive) + "\\" if BASE_DIR.drive else "/"
    total, used, free = shutil.disk_usage(path)
    return free / (1024 ** 3)

def clean_temp():
    if TEMP_DIR.exists():
        count = len(list(TEMP_DIR.iterdir()))
        if count > 0:
            shutil.rmtree(TEMP_DIR)
            TEMP_DIR.mkdir()
            print(f"  Temp: wiped {count} file(s)")
        else:
            print(f"  Temp: already clean")
    else:
        TEMP_DIR.mkdir(parents=True)
        print(f"  Temp: created fresh")

def clean_outputs(force=False):
    outputs = sorted(OUTPUT_DIR.glob("reel_*.mp4"), key=lambda f: f.stat().st_mtime)
    free_gb = get_free_space_gb()
    keep_n = LOW_SPACE_KEEP_N if (free_gb < MIN_FREE_SPACE_GB or force) else KEEP_LAST_N_OUTPUTS
    to_delete = outputs[:-keep_n] if len(outputs) > keep_n else []
    if to_delete:
        total_mb = sum(f.stat().st_size for f in to_delete) / (1024 ** 2)
        for f in to_delete:
            f.unlink()
        print(f"  Output: deleted {len(to_delete)} old Reel(s) ({total_mb:.0f} MB freed)")
    else:
        print(f"  Output: {len(outputs)} Reel(s) kept (under limit of {keep_n})")
    print(f"  Disk free: {free_gb:.1f} GB")

def check_temp_integrity(checkpoint):
    state = checkpoint.get("state", "idle")
    clip_files = [
        (f"clip_{i}_done", OUTPUT_DIR / "temp" / f"norm_{i}.mp4")
        for i in range(3)
    ]
    merge_file = OUTPUT_DIR / "temp" / "merged.mp4"
    encoded_file = checkpoint.get("output_file")
    if encoded_file:
        encoded_file = Path(encoded_file)

    if state == "encoded" or state == "uploaded":
        if encoded_file and not encoded_file.exists():
            print(f"  Missing encoded file — rolling back to 'merged'")
            state = "merged"

    if state == "merged":
        if not merge_file.exists():
            print(f"  Missing merged.mp4 — rolling back to check clips")
            state = "clip_2_done"

    for i in range(2, -1, -1):
        step = f"clip_{i}_done"
        norm_file = OUTPUT_DIR / "temp" / f"norm_{i}.mp4"
        if step_index(state) >= step_index(step):
            if not norm_file.exists():
                print(f"  Missing norm_{i}.mp4 — rolling back to clip_{i} start")
                prev = f"clip_{i-1}_done" if i > 0 else "videos_selected"
                state = prev

    if state != checkpoint.get("state"):
        return False, state
    return True, state

def step_index(s):
    steps = [
        "idle", "videos_selected",
        "clip_0_done", "clip_1_done", "clip_2_done",
        "merged", "encoded", "uploaded", "done"
    ]
    try:
        return steps.index(s)
    except ValueError:
        return 0

def full_cleanup():
    print("\n  Running post-run cleanup...")
    clean_temp()
    clean_outputs()

def startup_check(checkpoint):
    state = checkpoint.get("state", "idle")
    if state in ("idle", "done", "uploaded"):
        return checkpoint
    print(f"\n  Resuming from checkpoint: {state}")
    is_clean, corrected_state = check_temp_integrity(checkpoint)
    if not is_clean:
        print(f"  Checkpoint corrected: {state} -> {corrected_state}")
        checkpoint["state"] = corrected_state
    return checkpoint
