# evencues

Places memory and hot cues at a fixed bar interval throughout a Rekordbox
track, reading directly from Rekordbox's own beat grid analysis.

Built for consistent-interval cue placement (e.g. every 16 bars) rather than
structure-aware placement (intro/drop/breakdown) — useful when you want
predictable, evenly-spaced reference points for blending, independent of a
track's actual song structure.

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- Rekordbox (tested against Rekordbox 7.x's database format)

## Setup

```
git clone <this repo>
cd evencues
uv sync
```

## Usage

### Command line

```
uv run evencues preview "Playlist Name" "Track Name"
uv run evencues preview "Playlist Name" --all
uv run evencues apply "Playlist Name" --all --dry-run
uv run evencues apply "Playlist Name" --all
```

Rekordbox must be closed before `apply` writes (it refuses to write while the
app is open). `preview` and the `cues`/`grid` debug commands work with
Rekordbox open.

Key options:

| Flag | Default | What it does |
|---|---|---|
| `--bars` | 16 | Bars between each cue |
| `--max-hot` | 8 | Max hot cues (Rekordbox's real cap for Export-mode/hardware) |
| `--max-memory` | 10 | Max memory cues (Rekordbox has a known display bug past 10 — see below) |
| `--hot-position` | `last` | `last` reserves hot cues from the end of the track (mix-out points); `first` from the start |
| `--skip` | 0 | Seconds to skip before anchoring the pattern (e.g. past an acapella intro) |
| `--overwrite` | off | Replace existing cues instead of skipping tracks that already have them |
| `--all` | off | Run on every track in the playlist instead of one named track |

Debug commands:

```
uv run evencues cues "Playlist Name" "Track Name"   # list cues actually in master.db for this track
uv run evencues grid "Playlist Name" "Track Name"   # show raw beat grid data for this track
```

### Web interface

```
uv run evencues-web
```

Then open http://127.0.0.1:5151. On Windows, `start-web.bat` does this in one
double-click and opens the browser for you.

The web app is a thin frontend over the same `runner.py` logic the CLI uses —
neither one can drift out of sync with the other.

## How cue placement works

- Reads the actual per-beat grid from Rekordbox's analysis files (not BPM
  arithmetic), so placement doesn't drift on long tracks or tracks with minor
  grid adjustments.
- Anchors on a real downbeat, at or after any `--skip` offset — this exists
  because Rekordbox's own bar numbering can't express skipping a pickup
  bar/acapella intro (confirmed via Rekordbox support: bar numbers always run
  from time zero and can't be renumbered backward).
- Skips the mark exactly at the anchor point, since Rekordbox already places
  its own cue at the very start of every analyzed track.
- Hot cues are capped at 8 (Rekordbox's real Export-mode/hardware limit —
  Performance mode's 16-point A–P system doesn't carry into Export mode or
  survive to hardware).
- Memory cues are capped at 10 by default. Rekordbox has a documented bug
  where writing more than 10 breaks its own display (data is still written
  correctly to `master.db` — this is a UI bug, not data loss, but hasn't been
  verified against what actually reaches a USB export). Pass `--max-memory 0`
  to disable the cap once you've verified higher counts survive your export
  pipeline.
- Memory cue spacing is always an exact, fixed multiple of `--bars` — never
  auto-widened to fit more cues in. If a track has more candidate memory
  marks than the cap allows, the excess closest to the hot-cue block are
  dropped rather than spacing everything out further.

## Known limitations

- Track lookup matches on exact title within a playlist; if two tracks share
  an identical title, whichever pyrekordbox lists first is used.
- A track whose analysis files are missing/corrupted on disk is skipped with
  an error rather than crashing the batch — but its underlying Rekordbox
  analysis likely needs to be regenerated (right-click → re-analyze).
