"""Compute fixed-interval cue positions from a track's real beat grid."""

from __future__ import annotations


def compute_marks(
    times_ms: list[float],
    beat_numbers: list[int],
    bars_per_cue: int = 16,
    beats_per_bar: int = 4,
    skip_before_ms: float = 0.0,
) -> list[tuple[int, float]]:
    """Return (bar_number, position_ms) for every mark at every `bars_per_cue`-th
    bar, from one interval past the anchor to the end of the track.

    Anchors on the first downbeat (beat_numbers == 1) at or after `skip_before_ms`.
    This is a manual time offset rather than automatic detection: Rekordbox's own
    bar numbering can't express a pickup bar or acapella intro either (its bar
    count always runs from time zero, so it can show something like "2.1" for the
    real first downbeat but can't be renumbered to "1.1" — Rekordbox support has
    confirmed this isn't possible). Every beat is already correctly flagged 1-4
    within its bar regardless of what bar number Rekordbox displays, so searching
    for beat_numbers == 1 alone just finds the very first beat in the file — it
    doesn't skip acapella content. `skip_before_ms` is what actually does that.

    Deliberately skips the mark exactly at the anchor point too: Rekordbox
    automatically places its own cue at the very start of every analyzed
    track, so marking that position again here would just duplicate a point
    that's already there. The pattern starts one interval past the anchor —
    bar numbers returned reflect that (the first entry is bar `bars_per_cue`,
    not bar 0), and stay stable regardless of how callers later split marks
    between hot and memory cues.
    """
    if not times_ms or not beat_numbers:
        return []

    step = bars_per_cue * beats_per_bar
    if step <= 0:
        return []

    start = 0
    for i, (t, b) in enumerate(zip(times_ms, beat_numbers)):
        if t >= skip_before_ms and b == 1:
            start = i
            break

    positions = times_ms[start + step :: step]
    return [((i + 1) * bars_per_cue, pos) for i, pos in enumerate(positions)]


def plan_cue_assignment(
    marks: list[tuple[int, float]],
    max_hot: int = 8,
    max_memory: int | None = 10,
    hot_position: str = "last",
) -> tuple[list[tuple[int, float]], list[tuple[int, float]], list[tuple[int, float]]]:
    """Split marks into (hot_entries, mem_entries, dropped_entries).

    hot_position controls where the (up to 8) hot cues are reserved from:
      - "last": from the END of the track. Good for jumping straight to an
        outro/mix-out point — the scenario that actually needs a fast, no-look
        trigger, since intro-stage adjustments happen with the fader down and
        there's time to browse memory cues instead.
      - "first": from the START of the track (the original/simpler default).

    Either way, hot cues are reserved FIRST, before memory cues are assigned —
    this guarantees even a short track (fewer than 8 total marks) still gets
    hot cues, rather than having them crowded out by memory cues claiming
    everything.

    Memory cues fill whatever's left, kept at the exact base bar interval —
    never widened. If there are more candidate memory marks than max_memory
    allows, the excess (the ones closest to the hot block) are dropped rather
    than spacing everything out further: a fixed, known gap between memory
    cues is what makes them useful as a consistent reference for judging
    breakdowns/blend points by ear — widening the gap on longer tracks would
    break that.
    """
    n = len(marks)
    n_hot = min(max_hot, n)

    if hot_position == "first":
        hot_entries = marks[:n_hot]
        remaining = marks[n_hot:]
    else:
        hot_entries = marks[n - n_hot :] if n_hot else []
        remaining = marks[: n - n_hot]

    if max_memory is None or len(remaining) <= max_memory:
        return hot_entries, remaining, []

    mem_entries = remaining[:max_memory]
    dropped_entries = remaining[max_memory:]

    return hot_entries, mem_entries, dropped_entries
