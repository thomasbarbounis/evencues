"""evencues CLI — places memory and hot cues at a fixed bar interval.

    evencues preview "Playlist Name" "Track Name"
    evencues apply "Playlist Name" "Track Name" --dry-run
    evencues apply "Playlist Name" "Track Name"
"""

from __future__ import annotations

import click

from evencues.db import get_beat_grid
from evencues.runner import EvencuesError, apply_playlist, load_targets, preview_playlist


def _format_ms(ms: float) -> str:
    total_seconds = ms / 1000
    minutes = int(total_seconds // 60)
    seconds = total_seconds % 60
    return f"{minutes}:{seconds:04.1f}"


HOT_POSITION_OPTION = click.option(
    "--hot-position",
    type=click.Choice(["first", "last"]),
    default="last",
    show_default=True,
    help="Where to reserve hot cues: 'last' for jumping to an outro/mix-out point, 'first' for the track's intro.",
)


@click.group()
def cli():
    pass


@cli.command()
@click.argument("playlist")
@click.argument("track", required=False)
@click.option("--all", "all_tracks", is_flag=True, help="Run on every track in the playlist.")
@click.option("--bars", default=16, show_default=True, help="Bars between each cue.")
@click.option("--max-hot", default=8, show_default=True, help="Max hot cues to place (Rekordbox caps this at 8).")
@click.option("--max-memory", default=10, show_default=True, help="Max memory cues per track. Rekordbox's list widget has a known display bug past 10 — pass 0 for no cap once you've confirmed higher counts survive a real USB export.")
@click.option("--skip", "skip_seconds", default=0.0, show_default=True, help="Skip this many seconds from the start before anchoring the cue pattern (e.g. to skip past an acapella intro).")
@HOT_POSITION_OPTION
def preview(playlist, track, all_tracks, bars, max_hot, max_memory, skip_seconds, hot_position):
    """Show what would be placed, without writing anything.

        evencues preview "Playlist Name" "Track Name"
        evencues preview "Playlist Name" --all
    """
    mem_cap = None if max_memory <= 0 else max_memory
    try:
        plans = preview_playlist(playlist, track, all_tracks, bars, max_hot, mem_cap, skip_seconds, hot_position)
    except EvencuesError as e:
        raise click.ClickException(str(e))

    for i, plan in enumerate(plans):
        if i > 0:
            click.echo()

        if plan.error:
            click.echo(f"{plan.title} — SKIPPED: {plan.error}")
            continue

        click.echo(f"{plan.title} — BPM {plan.bpm:.1f}")
        summary = f"  {len(plan.marks)} marks every {bars} bars: {len(plan.hot_entries)} hot cues, {len(plan.mem_entries)} memory cues"
        if plan.dropped_entries:
            summary += f", {len(plan.dropped_entries)} dropped (past the {mem_cap}-memory-cue cap)"
        click.echo(summary)

        if not all_tracks:
            rows = [(bar, pos, "MEM", "") for bar, pos in plan.mem_entries]
            rows += [(bar, pos, "DROP", "") for bar, pos in plan.dropped_entries]
            hot_rows = [(bar, pos, "HOT", chr(65 + j)) for j, (bar, pos) in enumerate(plan.hot_entries)]
            rows = sorted(rows + hot_rows, key=lambda r: r[0])

            for bar, pos, kind, letter in rows:
                label = f"[{kind} {letter}]" if letter else f"[{kind}]"
                click.echo(f"  bar {bar:<5} {_format_ms(pos):>8}  {label}")

        if plan.existing_cues:
            click.echo(f"  Note: already has {plan.existing_cues} existing cue(s).")


@cli.command()
@click.argument("playlist")
@click.argument("track", required=False)
@click.option("--all", "all_tracks", is_flag=True, help="Run on every track in the playlist.")
@click.option("--bars", default=16, show_default=True, help="Bars between each cue.")
@click.option("--max-hot", default=8, show_default=True, help="Max hot cues to place (Rekordbox caps this at 8).")
@click.option("--dry-run", is_flag=True, help="Show what would be written without writing it.")
@click.option("--overwrite", is_flag=True, help="Delete existing cues before writing, without prompting.")
@click.option("--max-memory", default=10, show_default=True, help="Max memory cues per track. Rekordbox's list widget has a known display bug past 10 — pass 0 for no cap once you've confirmed higher counts survive a real USB export.")
@click.option("--skip", "skip_seconds", default=0.0, show_default=True, help="Skip this many seconds from the start before anchoring the cue pattern (e.g. to skip past an acapella intro).")
@HOT_POSITION_OPTION
def apply(playlist, track, all_tracks, bars, max_hot, dry_run, overwrite, max_memory, skip_seconds, hot_position):
    """Write memory + hot cues. Requires Rekordbox to be closed.

        evencues apply "Playlist Name" "Track Name"
        evencues apply "Playlist Name" --all
    """
    mem_cap = None if max_memory <= 0 else max_memory

    if not dry_run and not all_tracks and not overwrite:
        try:
            targets = load_targets(playlist, track, all_tracks)
        except EvencuesError as e:
            raise click.ClickException(str(e))
        from evencues.db import get_existing_cue_count
        count = get_existing_cue_count(targets[0])
        if count:
            click.confirm(f"{count} existing cue(s) found on this track. Overwrite?", abort=True)
            overwrite = True

    try:
        result = apply_playlist(playlist, track, all_tracks, bars, max_hot, mem_cap, skip_seconds, hot_position, dry_run, overwrite)
    except EvencuesError as e:
        raise click.ClickException(str(e))

    if not dry_run and all_tracks:
        with_existing = sum(1 for p in result["plans"] if p.existing_cues and not p.written and not p.error)
        if with_existing and not overwrite:
            click.echo(f"{with_existing} track(s) already had cues and were skipped (pass --overwrite to replace them).")

    for plan in result["plans"]:
        if plan.error:
            click.echo(f"{plan.title} — SKIPPED: {plan.error}")
        elif plan.skipped_reason:
            click.echo(f"{plan.title} — SKIPPED: {plan.skipped_reason}")
        else:
            click.echo(f"{plan.title} — {len(plan.mem_entries)} memory, {len(plan.hot_entries)} hot cues")

    if result["backup_path"]:
        click.echo(f"Backup: {result['backup_path']}")

    if dry_run:
        click.echo("\nDry run — no changes written.")
    else:
        click.echo(f"\nDone: {result['written']} track(s) written, {result['total_cues']} cues, {result['skipped']} skipped.")


@cli.command(name="live")
@click.argument("playlist")
@click.argument("track", required=False)
@click.option("--all", "all_tracks", is_flag=True, help="Run on every track in the playlist, walking it live via Load/Browse Down.")
@click.option("--bars", default=16, show_default=True, help="Bars between each cue.")
@click.option("--max-hot", default=8, show_default=True, help="Max hot cues to place (Rekordbox caps this at 8).")
@click.option("--max-memory", default=10, show_default=True, help="Max memory cues per track. Rekordbox's list widget has a known display bug past 10 — pass 0 for no cap once you've confirmed higher counts survive a real USB export.")
@click.option("--skip", "skip_seconds", default=0.0, show_default=True, help="Seconds into the track the anchor point sits at — must match wherever you've manually positioned the playhead.")
@click.option("--overwrite", is_flag=True, help="Delete existing cues on a track before placing new ones, instead of skipping it.")
@HOT_POSITION_OPTION
def live(playlist, track, all_tracks, bars, max_hot, max_memory, skip_seconds, overwrite, hot_position):
    """Place cues by driving a live Rekordbox deck over MIDI, instead of
    writing to the database directly.

    Unlike `apply`, cues placed this way reliably reach Rekordbox Cloud Sync
    (and therefore connected mobile devices) — because they go through
    Rekordbox's own controller-input code path instead of a direct database
    write. See midi_driver.py's module docstring for the one-time MIDI
    mapping this requires.

    Single track — load it onto the mapped deck yourself first, with the
    playhead at the intended anchor point (bar 1 / track start if --skip is
    0, otherwise manually seeked to the real first downbeat past --skip):

        evencues live "Playlist Name" "Track Name"

    Whole playlist — sort Rekordbox's browser by the Track Title column
    (ascending) first, since --all walks tracks in that order via Load/Browse
    Down, and highlight (don't load) the first track in the sorted list:

        evencues live "Playlist Name" --all

    Every cue placement is a *relative* beat jump from wherever the deck
    already was — there's no way to detect or correct a wrong starting
    position afterward.
    """
    mem_cap = None if max_memory <= 0 else max_memory

    from evencues.midi_driver import open_port, place_cues_for_playlist, place_cues_for_track

    if all_tracks:
        try:
            targets = load_targets(playlist, None, True)
        except EvencuesError as e:
            raise click.ClickException(str(e))
        targets = sorted(targets, key=lambda c: (c.Title or "").lower())

        click.confirm(
            f"Confirm: Rekordbox's browser is sorted by Track Title (ascending), and "
            f"{targets[0].Title!r} is highlighted (not loaded). Continue?",
            abort=True,
        )

        port = open_port()
        try:
            results = place_cues_for_playlist(port, targets, bars, skip_seconds, max_hot, mem_cap, hot_position, overwrite)
        finally:
            port.close()

        for result in results:
            if result.get("skipped_reason"):
                click.echo(f"{result['title']} — SKIPPED: {result['skipped_reason']}")
            else:
                click.echo(f"{result['title']} — {result['hot_set']} hot, {result['mem_set']} memory cues placed live")
        return

    try:
        content = load_targets(playlist, track, False)[0]
    except EvencuesError as e:
        raise click.ClickException(str(e))

    click.confirm(
        f"Confirm: {content.Title!r} is loaded on the mapped deck, and the playhead "
        f"is at the intended anchor point. Continue?",
        abort=True,
    )

    port = open_port()
    try:
        result = place_cues_for_track(port, content, bars, skip_seconds, max_hot, mem_cap, hot_position, overwrite)
    finally:
        port.close()

    if result.get("skipped_reason"):
        click.echo(f"{content.Title} — SKIPPED: {result['skipped_reason']}")
        return

    click.echo(f"{content.Title} — BPM {result['bpm']:.1f}")
    click.echo(
        f"{result['total_marks']} marks every {bars} bars: "
        f"{result['hot_set']} hot cues, {result['mem_set']} memory cues placed live"
    )
    if result["dropped"]:
        click.echo(f"{result['dropped']} dropped (past the memory-cue cap)")


@cli.command()
@click.argument("playlist")
@click.argument("track")
def cues(playlist, track):
    """List every cue currently stored in the database for this track — reads
    straight from master.db, bypassing Rekordbox's UI."""
    from evencues.db import get_db

    db = get_db()
    try:
        content = load_targets(playlist, track, False)[0]
    except EvencuesError as e:
        raise click.ClickException(str(e))

    rows = list(db.get_cue(ContentID=content.ID))
    rows.sort(key=lambda c: c.InMsec)

    click.echo(f"{content.Title} — {len(rows)} cue(s) in the database")
    for c in rows:
        kind_label = "MEM" if c.Kind == 0 else f"HOT(kind={c.Kind})"
        click.echo(f"  {_format_ms(c.InMsec):>8}  [{kind_label}]  {c.Comment or ''}")


@cli.command()
@click.argument("playlist")
@click.argument("track")
@click.option("--count", default=20, show_default=True, help="How many beat entries to show from the start.")
def grid(playlist, track, count):
    """Show the raw beat grid (time + beat-in-bar number) for the first N beats
    of a track, straight from the analysis file."""
    try:
        content = load_targets(playlist, track, False)[0]
    except EvencuesError as e:
        raise click.ClickException(str(e))

    times_ms, beat_numbers = get_beat_grid(content)
    if not times_ms:
        raise click.ClickException(f"Could not read a beat grid for {content.Title!r}.")

    click.echo(f"{content.Title} — {len(times_ms)} beats total")
    for i, (t, b) in enumerate(zip(times_ms[:count], beat_numbers[:count])):
        marker = "  <- downbeat" if b == 1 else ""
        click.echo(f"  [{i:>3}]  {_format_ms(t):>8}  beat {b}{marker}")


if __name__ == "__main__":
    cli()
