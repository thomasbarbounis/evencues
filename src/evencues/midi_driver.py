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

    MemoryCue Set       -> 9047
    HotCue1             -> 9048
    HotCue2             -> 9049
    HotCue3             -> 904A
    HotCue4             -> 904B
    HotCue5             -> 904C
    HotCue6             -> 904D
    HotCue7             -> 904E
    HotCue8             -> 904F
    BeatJump Pad2       -> 9051   (confirmed: exactly +1 bar per press, forward)
    Cue (DECK tab)      -> 9052   (moves the single Cue point to the current
                                   position — MemoryCue Set saves *that* point
                                   into the memory list, not the live playhead
                                   directly, so this must fire first)
    HotCue1 Delete      -> 9053
    HotCue2 Delete      -> 9054
    HotCue3 Delete      -> 9055
    HotCue4 Delete      -> 9056
    HotCue5 Delete      -> 9057
    HotCue6 Delete      -> 9058
    HotCue7 Delete      -> 9059
    HotCue8 Delete      -> 905A
    MemoryCue Delete    -> 905B
    MemoryCueCall Next  -> 905C
    MemoryCueCall Back  -> 905D
    Load (Deck 1)       -> 905E   (BROWSE tab — loads the currently
                                   highlighted track in the track list)
    Browse Down         -> 905F   (BROWSE tab — moves the highlight down
                                   one row; confirmed exact, like BeatJump)
    JumpToTrackStart    -> 9060   (DECK tab — jumps straight to absolute
                                   position 0, unlike Load, which was found
                                   to actually jump to wherever the deck's
                                   single Cue point currently sits, not to
                                   position 0 — coincidentally the same
                                   thing on a pristine, never-cued track,
                                   which is why Load looked reliable at
                                   first)

Confirmed against a real Rekordbox session: BROWSE-tab actions (Load, Browse
Down) and even native mouse/keyboard actions (double-click, Enter) silently
no-op unless Rekordbox's window actually has OS focus — this module calls
SetForegroundWindow before every browse/load action to guarantee that,
regardless of what else has focus when a batch run starts.

Before calling place_cues_for_track, the target track must already be loaded
on the deck this mapping points at (Deck 1, unless re-mapped), with the
playhead at the intended anchor point — bar 1 (track start) if skip_seconds
is 0, or manually seeked to the real first downbeat past the skip otherwise.
Every jump this module sends is a *relative* 1-bar-forward press, so there is
no way to detect or correct a wrong starting position afterward.

place_cues_for_playlist walks a whole playlist automatically using Load /
Browse Down — but it assumes the browser's current sort order matches
Content.Title ascending (Rekordbox's default "Track Title" column sort). If
the browser is sorted by something else when a batch run starts, Browse Down
will walk a different order than this module expects, and cues will land on
the wrong tracks. Sort the browser by Track Title before starting a batch.
"""

from __future__ import annotations

import ctypes
import time
from typing import Any

import mido

from evencues.db import get_beat_grid, get_bpm, get_db, get_existing_cue_count
from evencues.grid import compute_marks, plan_cue_assignment

PORT_NAME = "evencues 1"

NOTE_MEMORY_CUE_SET = 0x47
NOTE_HOT_CUE_BASE = 0x48  # Set: HotCue1..HotCue8 -> 0x48..0x4F
NOTE_BEATJUMP_FORWARD_1BAR = 0x51
NOTE_CUE_SET = 0x52
NOTE_HOT_CUE_DELETE_BASE = 0x53  # Delete: HotCue1..HotCue8 -> 0x53..0x5A
NOTE_MEMORY_CUE_DELETE = 0x5B
NOTE_MEMORY_CUE_CALL_NEXT = 0x5C
NOTE_MEMORY_CUE_CALL_BACK = 0x5D
NOTE_LOAD = 0x5E
NOTE_BROWSE_DOWN = 0x5F
NOTE_JUMP_TO_TRACK_START = 0x60

PRESS_HOLD_S = 0.03  # note_on -> note_off gap
PRESS_GAP_S = 0.15  # gap after note_off, before the next message
LOAD_SETTLE_S = 1.5  # time to let a newly-loaded track finish loading
CLEAR_SETTLE_S = 1.0  # extra settle before/after the delete burst in clear_all_cues


def open_port() -> "mido.ports.BaseOutput":
    return mido.open_output(PORT_NAME)


def focus_rekordbox() -> bool:
    """Bring Rekordbox's window to the OS foreground.

    Confirmed required for BROWSE-tab actions (Load, Browse Down) to do
    anything at all — without it they silently no-op, and so do native
    double-click/Enter-to-load, so this isn't specific to MIDI. Harmless (and
    a no-op besides the small sleep) for the DECK/PAD actions that don't
    require it. Returns False if Rekordbox isn't running.

    Windows silently blocks SetForegroundWindow from a process the user
    hasn't just interacted with (a background script, e.g.) — confirmed the
    hard way: this call worked in every manual PowerShell-driven test, then
    silently failed every time once it ran from this plain script instead.
    Attaching this thread's input to the current foreground window's thread
    first is the standard way around that restriction; a bare Alt key tap
    was tried first and rejected — it satisfies the same check, but Alt
    alone also activates Rekordbox's menu bar for keyboard navigation, which
    left the very next batch of beat-jump messages misdirected (confirmed:
    every hot cue landed at the same clamped end-of-track position instead
    of spreading out).
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    hwnd = user32.FindWindowW(None, "rekordbox")
    if not hwnd:
        return False
    current_thread = kernel32.GetCurrentThreadId()
    fg_hwnd = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None)
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    user32.AttachThreadInput(current_thread, fg_thread, True)
    user32.AttachThreadInput(current_thread, target_thread, True)
    try:
        user32.SetForegroundWindow(hwnd)
    finally:
        user32.AttachThreadInput(current_thread, fg_thread, False)
        user32.AttachThreadInput(current_thread, target_thread, False)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    return True


def _press(port: Any, note: int) -> None:
    port.send(mido.Message("note_on", channel=0, note=note, velocity=127))
    time.sleep(PRESS_HOLD_S)
    port.send(mido.Message("note_off", channel=0, note=note, velocity=0))
    time.sleep(PRESS_GAP_S)


def beatjump_forward(port: Any, bars: int) -> None:
    for _ in range(bars):
        _press(port, NOTE_BEATJUMP_FORWARD_1BAR)
    if bars:
        # Confirmed: firing a Cue/HotCue press immediately after a beatjump
        # burst finishes (no extra gap beyond each press's own PRESS_GAP_S)
        # can register against a stale, still-settling position — reran the
        # exact same sequence as isolated steps with real time between them
        # and it worked, then as one uninterrupted burst and it didn't.
        time.sleep(0.3)


def reset_to_track_start(port: Any) -> None:
    """Force the playhead back to absolute position 0.

    Reloading the track (Load) was used for this originally and looked
    100% reliable — until it wasn't: Load doesn't actually jump to position
    0, it jumps to wherever the deck's single Cue point currently sits.
    Those are the same thing on a pristine, never-cued track (hence why it
    looked reliable at first), but every memory cue we place moves the Cue
    point via `Cue` (see set_memory_cue), so by the time an overwrite run
    needs to reset, Load returns to the last spot a memory cue was placed,
    not to 0. JumpToTrackStart is a real "jump to absolute 0" deck function
    and doesn't have this problem.

    Only correct for a skip_seconds=0 anchor; if skip_seconds > 0, this still
    leaves the deck at absolute 0 rather than the real (skip-adjusted)
    anchor point, since there's no absolute-seek primitive to restore an
    arbitrary anchor after clearing disturbs the playhead.
    """
    _press(port, NOTE_JUMP_TO_TRACK_START)
    time.sleep(LOAD_SETTLE_S)


def set_hot_cue(port: Any, pad_index: int) -> None:
    """pad_index is 1-8, matching HotCue1..HotCue8."""
    _press(port, NOTE_HOT_CUE_BASE + (pad_index - 1))


def delete_hot_cue(port: Any, pad_index: int) -> None:
    """pad_index is 1-8, matching HotCue1 Delete..HotCue8 Delete."""
    _press(port, NOTE_HOT_CUE_DELETE_BASE + (pad_index - 1))


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


def clear_all_cues(port: Any, existing_memory_count: int) -> None:
    """Delete every hot cue pad (idempotent — deleting an empty pad is
    harmless) and every existing memory cue on the currently-loaded track.

    Memory cue deletion needs to start from before the very first memory
    cue — MemoryCueCall Back was tried for that and rejected: confirmed
    against a real deck to leave the earliest cue undeleted (repeated Back
    presses don't reliably clamp at the front the way BeatJump clamps at
    track start). JumpToTrackStart guarantees that starting point instead.
    """
    reset_to_track_start(port)
    time.sleep(CLEAR_SETTLE_S)

    for pad in range(1, 9):
        delete_hot_cue(port, pad)

    time.sleep(CLEAR_SETTLE_S)

    for _ in range(existing_memory_count):
        _press(port, NOTE_MEMORY_CUE_CALL_NEXT)
        time.sleep(0.3)  # let the deck finish jumping to the cue before deleting it
        _press(port, NOTE_MEMORY_CUE_DELETE)

    time.sleep(CLEAR_SETTLE_S)


def load_highlighted_track(port: Any) -> None:
    focus_rekordbox()
    _press(port, NOTE_LOAD)
    time.sleep(LOAD_SETTLE_S)


def browse_down(port: Any) -> None:
    focus_rekordbox()
    _press(port, NOTE_BROWSE_DOWN)


def _existing_memory_cue_count(content: Any) -> int:
    return sum(1 for c in get_db().get_cue(ContentID=content.ID) if c.Kind == 0)


def place_cues_for_track(
    port: Any,
    content: Any,
    bars: int,
    skip_seconds: float,
    max_hot: int,
    max_memory: int | None,
    hot_position: str,
    overwrite: bool = False,
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

    Assumes the track is already loaded with the playhead at the intended
    anchor point. If overwrite is True, clears every existing hot/memory cue
    on the currently-loaded track first.
    """
    existing = get_existing_cue_count(content)
    if existing and not overwrite:
        return {"skipped_reason": f"has {existing} existing cue(s), overwrite not enabled"}

    times_ms, beat_numbers = get_beat_grid(content)
    if not times_ms:
        raise ValueError("Could not read a beat grid — is it analyzed in Rekordbox?")

    marks = compute_marks(times_ms, beat_numbers, bars_per_cue=bars, skip_before_ms=skip_seconds * 1000)
    hot_entries, mem_entries, dropped_entries = plan_cue_assignment(marks, max_hot, max_memory, hot_position)

    if overwrite and existing:
        clear_all_cues(port, _existing_memory_cue_count(content))
        # Memory-cue deletion navigates the playhead to each cue before
        # removing it, leaving it wherever the last one was — not back at
        # the anchor. Confirmed bug: without this, every subsequent
        # placement lands offset by that leftover position. Only correct
        # for a skip_seconds=0 anchor (see reset_to_track_start).
        reset_to_track_start(port)

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


def place_cues_for_playlist(
    port: Any,
    tracks: list,
    bars: int,
    skip_seconds: float,
    max_hot: int,
    max_memory: int | None,
    hot_position: str,
    overwrite: bool = False,
) -> list[dict]:
    """Walk a whole playlist via Load/Browse Down, placing cues on each
    track in turn. `tracks` must be in the exact order Rekordbox's browser
    is currently sorted in (see module docstring) — the FIRST track in that
    list must already be highlighted (not loaded) in Rekordbox before
    calling this.

    Returns one result dict per track, each with a "title" key added.
    """
    results = []
    for i, content in enumerate(tracks):
        if i > 0:
            browse_down(port)
        load_highlighted_track(port)

        try:
            result = place_cues_for_track(
                port, content, bars, skip_seconds, max_hot, max_memory, hot_position, overwrite
            )
        except ValueError as e:
            result = {"skipped_reason": str(e)}

        result["title"] = content.Title
        results.append(result)

    return results
