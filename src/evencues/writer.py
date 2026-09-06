"""Write memory and hot cues to the Rekordbox database.

The DjmdCue field structure and the backup-before-write pattern here follow
what djcues (github.com/mcroydon/djcues) uses in production — those Kind
values in particular are verified against real Rekordbox DB rows, not
guessed, so they're reused as-is.
"""

from __future__ import annotations

import pathlib
import shutil
from datetime import datetime
from uuid import uuid4

# Rekordbox's internal Kind values for hot cue pads A-H, in order.
# Note Kind 4 is skipped entirely — this isn't a typo, it matches verified DB rows.
HOT_CUE_KINDS = [1, 2, 3, 5, 6, 7, 8, 9]

# "CDJ" style: every hot cue the same green, matching what actual CDJ hardware
# displays (pre-Nexus2 players could only light a hot cue pad one color, so
# Rekordbox's "CDJ" cue-color preset makes every new hot cue green rather than
# assigning a rainbow). ColorTableIndex 18 is verified green — same value used
# for green-labeled hot cues in djcues' cue system.
HOT_CUE_COLOR_TABLE_INDEX = 18

MEMORY_CUE_COLOR = 4  # Green, in Rekordbox's 8-color memory cue palette


def backup_database(db_path: pathlib.Path) -> pathlib.Path:
    """Copy master.db to a timestamped backup file. Returns the backup path."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_name = f"{db_path.stem}-backup-{timestamp}.db"
    backup_path = db_path.parent / backup_name
    shutil.copy2(db_path, backup_path)
    return backup_path


def _cue_row(position_ms: float, kind: int, color_table_index: int | None, color: int, comment: str, active_loop: int = -1) -> dict:
    return {
        "Kind": kind,
        "InMsec": int(position_ms),
        "InFrame": 0,
        "InMpegFrame": 0,
        "InMpegAbs": 0,
        "OutMsec": -1,
        "OutFrame": -1,
        "OutMpegFrame": -1,
        "OutMpegAbs": -1,
        "Color": color,
        "ColorTableIndex": color_table_index,
        "ActiveLoop": active_loop,
        "Comment": comment,
        "BeatLoopSize": 0,
        "CueMicrosec": 0,
    }


def build_rows(hot_entries: list[tuple[int, float]], mem_entries: list[tuple[int, float]]) -> tuple[list[dict], list[dict]]:
    """Build (hot_rows, mem_rows) field dicts ready for DjmdCue creation, from
    already-decided (bar_number, position_ms) entries — see
    grid.plan_cue_assignment for how those are chosen. Hot cues are assigned
    to pads A-H in order; only the first 8 entries are used since that's
    Rekordbox's hard cap on hot cue pads.
    """
    hot_rows: list[dict] = []
    for i, (bar_number, pos) in enumerate(hot_entries[: len(HOT_CUE_KINDS)]):
        kind = HOT_CUE_KINDS[i]
        pad_letter = chr(65 + i)
        hot_rows.append(_cue_row(pos, kind=kind, color_table_index=HOT_CUE_COLOR_TABLE_INDEX, color=-1, comment=f"Hot {pad_letter} (Bar {bar_number})"))

    mem_rows: list[dict] = [
        # ColorTableIndex=0 and ActiveLoop=0 (not None/-1) match what Rekordbox's own
        # AutoCue feature writes for memory cues — confirmed synced to Rekordbox Cloud
        # (and thus to mobile devices), unlike our previous None/-1 defaults.
        _cue_row(pos, kind=0, color_table_index=0, color=MEMORY_CUE_COLOR, comment=f"Bar {bar_number}", active_loop=0)
        for bar_number, pos in mem_entries
    ]

    return hot_rows, mem_rows


def write_cues_for_track(db, content, hot_rows: list[dict], mem_rows: list[dict], overwrite: bool = False) -> int:
    """Write cue rows to the DB for one track. If overwrite is True, deletes
    existing cues on the track first. Returns the count of cues written.

    Caller is responsible for calling db.commit() afterward.
    """
    from pyrekordbox.db6 import tables

    if overwrite:
        existing = db.get_cue(ContentID=content.ID)
        for cue in existing:
            db.delete(cue)

    count = 0
    for row in hot_rows + mem_rows:
        cue_id = db.generate_unused_id(tables.DjmdCue)
        cue = tables.DjmdCue.create(
            ID=str(cue_id),
            ContentID=str(content.ID),
            ContentUUID=content.UUID,
            UUID=str(uuid4()),
            **row,
        )
        db.add(cue)
        count += 1

    return count
