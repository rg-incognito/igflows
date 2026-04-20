"""
Checkpoint manager — saves/loads pipeline state so runs can resume mid-way.
"""

import json
import os
from pathlib import Path
from datetime import datetime

CHECKPOINT_FILE = Path(os.environ.get("PIPELINE_BASE_DIR", ".")) / "checkpoint.json"

STEPS = [
    "idle",
    "videos_selected",
    "clip_0_done",
    "clip_1_done",
    "clip_2_done",
    "merged",
    "encoded",
    "uploaded",
    "done"
]

def step_index(state):
    try:
        return STEPS.index(state)
    except ValueError:
        return 0

def past(current_state, target_state):
    return step_index(current_state) >= step_index(target_state)

def load():
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE) as f:
            return json.load(f)
    return {"state": "idle"}

def save(state, **kwargs):
    data = load()
    data["state"] = state
    data["updated_at"] = datetime.now().isoformat()
    data.update(kwargs)
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(data, f, indent=2)

def clear():
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump({"state": "idle"}, f)
