"""Shared logic for computing and writing cue plans.

Both cli.py and webapp.py call into this module rather than duplicating logic,
so the terminal and the browser interface can't drift out of sync with each
other — a bug class this project has hit more than once when two entry points
each had their own copy of the same computation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evencues.db import (
    find_playlist,
    find_track_in_playlist,
    get_beat_grid,
    get_bpm,
    get_db,
    get_existing_cue_count,
    get_playlist_tracks,
)
from evencues.grid import compute_marks, plan_cue_assignment
from evencues.writer import backup_database, build_rows, write_cues_for_track


class EvencuesError(Exception):
    """Raised for user-facing problems (bad playlist/track name, etc.)."""


@dataclass
class TrackPlan:
    content: Any
    title: str
    bpm: float | None = None
    marks: list[tuple[int, float]] = field(default_factory=list)
    hot_entries: list[tuple[int, float]] = field(default_factory=list)
    mem_entries: list[tuple[int, float]] = field(default_factory=list)
    dropped_entries: list[tuple[int, float]] = field(default_factory=list)
    existing_cues: int = 0
    error: str | None = None
    written: bool = False
    skipped_reason: str | None = None


def load_targets(playlist_name: str, track_name: str | None, all_tracks: bool) -> list:
    """Resolve the list of tracks to operate on, given playlist/track/--all."""
    if all_tracks and track_name:
        raise EvencuesError("Pass either a track name or 'all tracks', not both.")
    if not all_tracks and not track_name:
        raise EvencuesError("Pass a track name, or choose 'all tracks' for the whole playlist.")

    playlist = find_playlist(playlist_name)
    if playlist is None:
        raise EvencuesError(f"Playlist not found: {playlist_name!r}")

    if all_tracks:
        tracks = get_playlist_tracks(playlist)
        if not tracks:
            raise EvencuesError(f"Playlist {playlist_name!r} has no tracks.")
        return tracks

    content = find_track_in_playlist(playlist, track_name)
    if content is None:
        raise EvencuesError(f"Track not found in {playlist_name!r}: {track_name!r}")
    return [content]


def plan_track(
    content,
    bars: int,
    skip_seconds: float,
    max_hot: int,
    max_memory: int | None,
    hot_position: str,
) -> TrackPlan:
    """Compute the full cue plan for one track, without writing anything."""
    plan = TrackPlan(content=content, title=content.Title, existing_cues=get_existing_cue_count(content))

    try:
        bpm = get_bpm(content)
        times_ms, beat_numbers = get_beat_grid(content)
        if not times_ms:
            plan.error = "Could not read a beat grid — is it analyzed in Rekordbox?"
            return plan

        marks = compute_marks(times_ms, beat_numbers, bars_per_cue=bars, skip_before_ms=skip_seconds * 1000)
        hot_entries, mem_entries, dropped_entries = plan_cue_assignment(marks, max_hot, max_memory, hot_position)

        plan.bpm = bpm
        plan.marks = marks
        plan.hot_entries = hot_entries
        plan.mem_entries = mem_entries
        plan.dropped_entries = dropped_entries
    except Exception as e:  # noqa: BLE001 — surfaced to the caller as plan.error, not raised
        plan.error = str(e)

    return plan


def preview_playlist(
    playlist_name: str,
    track_name: str | None,
    all_tracks: bool,
    bars: int,
    max_hot: int,
    max_memory: int | None,
    skip_seconds: float,
    hot_position: str,
) -> list[TrackPlan]:
    """Compute plans for every target track — read-only, writes nothing."""
    targets = load_targets(playlist_name, track_name, all_tracks)
    return [plan_track(c, bars, skip_seconds, max_hot, max_memory, hot_position) for c in targets]


def apply_playlist(
    playlist_name: str,
    track_name: str | None,
    all_tracks: bool,
    bars: int,
    max_hot: int,
    max_memory: int | None,
    skip_seconds: float,
    hot_position: str,
    dry_run: bool,
    overwrite: bool,
) -> dict:
    """Write cues for every target track. Returns a summary dict with per-track
    TrackPlan results (each annotated with .written / .skipped_reason) plus
    aggregate counts. Requires Rekordbox to be closed unless dry_run=True.
    """
    db = get_db()
    targets = load_targets(playlist_name, track_name, all_tracks)

    plans: list[TrackPlan] = []
    backed_up = False
    written = 0
    total_cues = 0
    skipped = 0
    backup_path = None

    for content in targets:
        plan = plan_track(content, bars, skip_seconds, max_hot, max_memory, hot_position)
        plans.append(plan)

        if plan.error:
            skipped += 1
            continue

        if plan.existing_cues and not overwrite:
            plan.skipped_reason = f"has {plan.existing_cues} existing cue(s), overwrite not enabled"
            skipped += 1
            continue

        if dry_run:
            continue

        hot_rows, mem_rows = build_rows(plan.hot_entries, plan.mem_entries)

        if not backed_up:
            backup_path = backup_database(db.db_directory / "master.db")
            backed_up = True

        count = write_cues_for_track(db, content, hot_rows, mem_rows, overwrite=bool(plan.existing_cues))
        db.commit()
        plan.written = True
        written += 1
        total_cues += count

    return {
        "plans": plans,
        "written": written,
        "total_cues": total_cues,
        "skipped": skipped,
        "dry_run": dry_run,
        "backup_path": str(backup_path) if backup_path else None,
    }
