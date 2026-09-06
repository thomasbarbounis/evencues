"""Place cues in a live Rekordbox deck via MIDI, instead of writing directly
to master.db.

Why this exists: cues written straight to the database (see writer.py) don't
reliably reach Rekordbox Cloud Sync, and therefore never show up on a
connected mobile device — confirmed by extensive testing against a real
account. Cues placed through Rekordbox's own MIDI-mappable actions go through
the same code path as a real hardware controller press, and do sync — also
confirmed against a real account.

One-time setup required in Rekordbox (Performance mode -> MIDI button, with
a virtual MIDI port such as loopMIDI's "evencues" port already connected):
add these functions and set their MIDI IN codes exactly as follows —

    MemoryCue Set     -> 9047
    HotCue1           -> 9048
    HotCue2           -> 9049
    HotCue3           -> 904A
    HotCue4           -> 904B
    HotCue5           -> 904C
    HotCue6           -> 904D
    HotCue7           -> 904E
    HotCue8           -> 904F
    BeatJump Pad2     -> 9051   (confirmed: exactly +1 bar per press, forward)
    Cue (DECK tab)    -> 9052   (moves the single Cue point to the current
                                 position — MemoryCue Set saves *that* point
                                 into the memory list, not the live playhead
                                 directly, so this must fire first)

Before calling place_cues_for_track, the target track must already be loaded
on the deck this mapping points at (Deck 1, unless re-mapped), with the
playhead at the intended anchor point — bar 1 (track start) if skip_seconds
is 0, or manually seeked to the real first downbeat past the skip otherwise.
Every jump this module sends is a *relative* 1-bar-forward press, so there is
no way to detect or correct a wrong starting position afterward.
"""

from __future__ import annotations

import time
from typing import Any

import mido

from evencues.db import get_beat_grid, get_bpm
from evencues.grid import compute_marks, plan_cue_assignment

PORT_NAME = "evencues 1"

NOTE_MEMORY_CUE_SET = 0x47
NOTE_HOT_CUE_BASE = 0x48  # HotCue1..HotCue8 -> 0x48..0x4F
NOTE_BEATJUMP_FORWARD_1BAR = 0x51
NOTE_CUE_SET = 0x52

PRESS_HOLD_S = 0.03  # note_on -> note_off gap
PRESS_GAP_S = 0.12  # gap after note_off, before the next message


def open_port() -> "mido.ports.BaseOutput":
    return mido.open_output(PORT_NAME)


def _press(port: Any, note: int) -> None:
    port.send(mido.Message("note_on", channel=0, note=note, velocity=127))
    time.sleep(PRESS_HOLD_S)
    port.send(mido.Message("note_off", channel=0, note=note, velocity=0))
    time.sleep(PRESS_GAP_S)


def beatjump_forward(port: Any, bars: int) -> None:
    for _ in range(bars):
        _press(port, NOTE_BEATJUMP_FORWARD_1BAR)


def set_hot_cue(port: Any, pad_index: int) -> None:
    """pad_index is 1-8, matching HotCue1..HotCue8."""
    _press(port, NOTE_HOT_CUE_BASE + (pad_index - 1))


def set_cue_point(port: Any) -> None:
    _press(port, NOTE_CUE_SET)


def set_memory_cue(port: Any) -> None:
    # MemoryCue Set saves the deck's single Cue point into the memory list —
    # it does NOT read the live playhead directly. Without moving the Cue
    # point first, every call just re-saves wherever it already was (track
    # start, for an untouched analyzed track), confirmed against a real deck.
    set_cue_point(port)
    time.sleep(0.15)
    _press(port, NOTE_MEMORY_CUE_SET)


def place_cues_for_track(
    port: Any,
    content: Any,
    bars: int,
    skip_seconds: float,
    max_hot: int,
    max_memory: int | None,
    hot_position: str,
) -> dict:
    """Compute the same hot/memory cue plan the database-writing path uses,
    then place it live via MIDI. Returns a summary dict.

    Uses only the bar-number half of each computed mark (the position_ms
    half is irrelevant here — Rekordbox's own beat jump does the real
    navigation, so there's nothing to gain from our own timestamp math).
    Marks are always spaced exactly `bars` apart by construction of
    compute_marks, so walking their bar-number deltas in order reaches each
    one with an exact 1-bar-forward press count, regardless of where the
    anchor point actually falls in the track.
    """
    times_ms, beat_numbers = get_beat_grid(content)
    if not times_ms:
        raise ValueError("Could not read a beat grid — is it analyzed in Rekordbox?")

    marks = compute_marks(times_ms, beat_numbers, bars_per_cue=bars, skip_before_ms=skip_seconds * 1000)
    hot_entries, mem_entries, dropped_entries = plan_cue_assignment(marks, max_hot, max_memory, hot_position)

    hot_bars = {bar for bar, _ in hot_entries}
    mem_bars = {bar for bar, _ in mem_entries}
    hot_pad_by_bar = {bar: i + 1 for i, (bar, _) in enumerate(hot_entries)}

    all_bars = sorted(bar for bar, _ in (hot_entries + mem_entries + dropped_entries))

    current_bar = 0
    hot_set = 0
    mem_set = 0
    for bar in all_bars:
        beatjump_forward(port, bar - current_bar)
        current_bar = bar
        if bar in hot_bars:
            set_hot_cue(port, hot_pad_by_bar[bar])
            hot_set += 1
        elif bar in mem_bars:
            set_memory_cue(port)
            mem_set += 1

    return {
        "bpm": get_bpm(content),
        "total_marks": len(marks),
        "hot_set": hot_set,
        "mem_set": mem_set,
        "dropped": len(dropped_entries),
    }
