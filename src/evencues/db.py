"""Read the beat grid, BPM, and track/playlist info from the Rekordbox database.

This is a trimmed-down version of the pattern used by djcues (github.com/mcroydon/djcues) —
same underlying pyrekordbox calls, but without phrase, vocal, or waveform extraction,
since evencues doesn't need any of that.
"""

from __future__ import annotations

import logging
from typing import Any

from pyrekordbox import Rekordbox6Database

logger = logging.getLogger(__name__)

_db: Rekordbox6Database | None = None


def get_db() -> Rekordbox6Database:
    """Get or create the shared database connection."""
    global _db
    if _db is None:
        _db = Rekordbox6Database()
    return _db


def find_playlist(name: str) -> Any | None:
    """Find a playlist by exact name. Prefers actual playlists over folders."""
    db = get_db()
    folder_match = None
    for pl in db.get_playlist():
        if pl.Name == name:
            if pl.Attribute != 1:
                return pl
            folder_match = pl
    return folder_match


def get_playlist_tracks(playlist: Any) -> list[Any]:
    """Return every track (Content object) in a playlist, in playlist order."""
    db = get_db()
    return [song.Content for song in db.get_playlist_songs(PlaylistID=playlist.ID)]


def list_playlist_names() -> list[str]:
    """Return sorted names of real playlists, excluding folders."""
    db = get_db()
    names = {pl.Name for pl in db.get_playlist() if pl.Attribute != 1 and pl.Name}
    return sorted(names)


def list_track_titles(playlist_name: str) -> list[str]:
    """Return track titles, in playlist order, for a playlist by exact name.

    Empty list if the playlist doesn't exist — callers treat "no options"
    the same whether that's because the playlist is empty or missing.
    """
    playlist = find_playlist(playlist_name)
    if playlist is None:
        return []
    return [content.Title for content in get_playlist_tracks(playlist)]


def find_track_in_playlist(playlist: Any, track_name: str) -> Any | None:
    """Find a track by exact title within a playlist.

    Note: if two tracks in the playlist share the same title, this returns
    whichever one pyrekordbox lists first. Fine for most libraries, but worth
    knowing if you have duplicate titles.
    """
    db = get_db()
    for song in db.get_playlist_songs(PlaylistID=playlist.ID):
        if song.Content.Title == track_name:
            return song.Content
    return None


def get_beat_grid(track_content: Any) -> tuple[list[float], list[int]]:
    """Return (times_ms, beat_numbers) for every beat in the track, as Rekordbox's
    own analysis placed them. beat_numbers gives each entry's position within its
    bar (1, 2, 3, or 4 for 4/4) — this is what "Set First Beat" in Rekordbox's grid
    editor actually changes, so cue placement anchors to a real downbeat (beat == 1)
    rather than assuming the very first entry in the list is one. That assumption
    breaks on tracks with a pickup bar or acapella intro before the "real" first bar.

    Reads the "PQTZ" tag from the .DAT file — this is the full per-beat grid,
    one entry per beat across the whole track. (There's also a "PQT2" tag in the
    .EXT file, but that one is a compact tempo-segment marker, not a per-beat
    listing — for a constant-BPM track it holds a single entry for the entire
    track, which is useless here. Worth remembering in case a future track has
    real tempo changes and needs different handling.)

    This is deliberately used instead of computing beat positions from BPM
    arithmetic: pure `first_beat + n * bar_duration` math assumes a perfectly
    constant tempo and drifts out of alignment with Rekordbox's real grid as
    you move further into the track.
    """
    db = get_db()
    times_ms: list[float] = []
    beat_numbers: list[int] = []

    try:
        anlz_files = db.read_anlz_files(track_content)
    except (FileNotFoundError, OSError):
        # The database has a record for this track, but its analysis files are
        # missing or unreachable on disk (moved, deleted, or never fully
        # analyzed). Treat it the same as "no beat grid" rather than crashing
        # the whole batch — the caller already handles an empty result as a
        # per-track skip.
        return times_ms, beat_numbers

    for path, af in anlz_files.items():
        if path.suffix == ".DAT":
            for tag in af.tags:
                if type(tag).__name__ == "PQTZAnlzTag":
                    beats_arr, _bpms, times_arr = tag.get()
                    times_ms = [float(t) * 1000 for t in times_arr]
                    beat_numbers = [int(b) for b in beats_arr]
                    break

    return times_ms, beat_numbers


def get_bpm(track_content: Any) -> float:
    """Return the track's stored BPM (for display only — cue placement uses
    real beat timestamps, not this value)."""
    return track_content.BPM / 100


def get_duration_ms(track_content: Any) -> float:
    """Return the track's length in milliseconds."""
    return float(track_content.Length or 0) * 1000


def get_existing_cue_count(track_content: Any) -> int:
    """Return how many cues (hot + memory) already exist on this track."""
    db = get_db()
    return len(list(db.get_cue(ContentID=track_content.ID)))
