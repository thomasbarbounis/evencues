"""Local web interface for evencues.

Run with:
    uv run evencues-web

Then open http://127.0.0.1:5151 in your browser. This has to run locally
(not hosted anywhere) since it needs direct access to your Rekordbox
installation on this machine — the browser is just the UI, the actual work
still happens on your computer.
"""

from __future__ import annotations

from flask import Flask, render_template_string, request

from evencues.runner import EvencuesError, apply_playlist, preview_playlist

app = Flask(__name__)

PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>evencues</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: -apple-system, Segoe UI, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px;
         background: #14161a; color: #e6e6e6; }
  h1 { font-size: 22px; margin-bottom: 4px; }
  .sub { color: #999; margin-bottom: 24px; font-size: 14px; }
  form { background: #1e2126; border: 1px solid #2c3038; border-radius: 10px; padding: 20px; margin-bottom: 24px; }
  .row { display: flex; gap: 16px; margin-bottom: 14px; flex-wrap: wrap; }
  .field { flex: 1; min-width: 140px; }
  label { display: block; font-size: 12px; color: #aaa; margin-bottom: 4px; }
  input[type=text], input[type=number] {
    width: 100%; padding: 8px; border-radius: 6px; border: 1px solid #3a3f47; background: #14161a; color: #eee;
    box-sizing: border-box;
  }
  .checks { display: flex; gap: 20px; margin: 14px 0; flex-wrap: wrap; }
  .checks label { display: flex; align-items: center; gap: 6px; font-size: 13px; color: #ccc; }
  .radios { display: flex; gap: 16px; margin: 6px 0 14px; }
  .radios label { display: flex; align-items: center; gap: 6px; font-size: 13px; color: #ccc; }
  button {
    padding: 10px 18px; border-radius: 6px; border: none; font-size: 14px; cursor: pointer; font-weight: 600;
  }
  .btn-preview { background: #3a3f47; color: #eee; margin-right: 10px; }
  .btn-apply { background: #c0392b; color: #fff; }
  .btn-preview:hover { background: #4a505a; }
  .btn-apply:hover { background: #d84b3a; }
  .results { background: #1e2126; border: 1px solid #2c3038; border-radius: 10px; padding: 20px; }
  .track { border-bottom: 1px solid #2c3038; padding: 10px 0; }
  .track:last-child { border-bottom: none; }
  .track-title { font-weight: 600; }
  .track-summary { color: #aaa; font-size: 13px; margin-top: 2px; }
  .error { color: #e08080; }
  .skip { color: #d9a441; }
  .ok { color: #7fbf7f; }
  .marks { margin-top: 8px; font-family: monospace; font-size: 12px; color: #bbb; white-space: pre; }
  .hot { color: #7fbf7f; }
  .mem { color: #7fa8d9; }
  .drop { color: #666; text-decoration: line-through; }
  .summary-line { margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid #2c3038; font-size: 13px; color: #ccc; }
  .warn-banner { background: #3a2a1a; border: 1px solid #7a5a2a; color: #e0b070; border-radius: 8px; padding: 10px 14px; margin-bottom: 16px; font-size: 13px; }
</style>
</head>
<body>
  <h1>evencues</h1>
  <div class="sub">Places memory + hot cues at a fixed bar interval in Rekordbox.</div>

  <div class="warn-banner">Close Rekordbox before clicking Apply — writes are blocked while it's open (Preview works fine with it open).</div>

  <form method="post" action="/run">
    <div class="row">
      <div class="field">
        <label>Playlist name</label>
        <input type="text" name="playlist" value="{{ f.playlist }}" required>
      </div>
      <div class="field">
        <label>Track name (leave blank for whole playlist)</label>
        <input type="text" name="track" value="{{ f.track }}">
      </div>
    </div>

    <div class="row">
      <div class="field">
        <label>Bars between cues</label>
        <input type="number" name="bars" value="{{ f.bars }}">
      </div>
      <div class="field">
        <label>Max hot cues</label>
        <input type="number" name="max_hot" value="{{ f.max_hot }}">
      </div>
      <div class="field">
        <label>Max memory cues (0 = no cap)</label>
        <input type="number" name="max_memory" value="{{ f.max_memory }}">
      </div>
      <div class="field">
        <label>Skip seconds from start</label>
        <input type="number" step="0.1" name="skip_seconds" value="{{ f.skip_seconds }}">
      </div>
    </div>

    <label>Hot cue position</label>
    <div class="radios">
      <label><input type="radio" name="hot_position" value="last" {{ 'checked' if f.hot_position == 'last' else '' }}> Last (end of track — mix-out points)</label>
      <label><input type="radio" name="hot_position" value="first" {{ 'checked' if f.hot_position == 'first' else '' }}> First (start of track)</label>
    </div>

    <div class="checks">
      <label><input type="checkbox" name="all_tracks" {{ 'checked' if f.all_tracks else '' }}> Whole playlist (--all)</label>
      <label><input type="checkbox" name="overwrite" {{ 'checked' if f.overwrite else '' }}> Overwrite existing cues</label>
    </div>

    <button class="btn-preview" type="submit" name="action" value="preview">Preview</button>
    <button class="btn-apply" type="submit" name="action" value="apply" onclick="return confirm('This writes to your Rekordbox database. Backups are automatic, but make sure Rekordbox is closed. Continue?');">Apply</button>
  </form>

  {% if error %}
    <div class="results"><div class="error">{{ error }}</div></div>
  {% elif plans is not none %}
    <div class="results">
      {% if summary %}
        <div class="summary-line">{{ summary }}</div>
      {% endif %}
      {% for p in plans %}
        <div class="track">
          <div class="track-title">{{ p.title }}</div>
          {% if p.error %}
            <div class="track-summary error">SKIPPED: {{ p.error }}</div>
          {% elif p.skipped_reason %}
            <div class="track-summary skip">SKIPPED: {{ p.skipped_reason }}</div>
          {% else %}
            <div class="track-summary">
              BPM {{ "%.1f"|format(p.bpm) }} —
              {{ p.hot_entries|length }} hot cue(s), {{ p.mem_entries|length }} memory cue(s)
              {% if p.dropped_entries %}, {{ p.dropped_entries|length }} dropped{% endif %}
              {% if p.written %} <span class="ok">— written</span>{% endif %}
              {% if p.existing_cues and not p.written %} — already has {{ p.existing_cues }} existing cue(s){% endif %}
            </div>
            {% if show_detail %}
            <div class="marks">{% for line in p.detail_lines %}{{ line }}
{% endfor %}</div>
            {% endif %}
          {% endif %}
        </div>
      {% endfor %}
    </div>
  {% endif %}
</body>
</html>
"""


def _default_form():
    return {
        "playlist": "",
        "track": "",
        "all_tracks": False,
        "bars": 16,
        "max_hot": 8,
        "max_memory": 10,
        "skip_seconds": 0.0,
        "hot_position": "last",
        "overwrite": False,
    }


def _read_form(form) -> dict:
    return {
        "playlist": form.get("playlist", "").strip(),
        "track": form.get("track", "").strip() or None,
        "all_tracks": form.get("all_tracks") == "on",
        "bars": int(form.get("bars") or 16),
        "max_hot": int(form.get("max_hot") or 8),
        "max_memory": int(form.get("max_memory") or 10),
        "skip_seconds": float(form.get("skip_seconds") or 0.0),
        "hot_position": form.get("hot_position", "last"),
        "overwrite": form.get("overwrite") == "on",
    }


def _format_ms(ms: float) -> str:
    total_seconds = ms / 1000
    minutes = int(total_seconds // 60)
    seconds = total_seconds % 60
    return f"{minutes}:{seconds:04.1f}"


def _detail_lines(plan) -> list[str]:
    rows = [(bar, pos, "mem", "MEM") for bar, pos in plan.mem_entries]
    rows += [(bar, pos, "drop", "DROPPED") for bar, pos in plan.dropped_entries]
    rows += [(bar, pos, "hot", f"HOT {chr(65 + j)}") for j, (bar, pos) in enumerate(plan.hot_entries)]
    rows.sort(key=lambda r: r[0])
    return [f"bar {bar:<5} {_format_ms(pos):>8}  [{label}]" for bar, pos, _cls, label in rows]


@app.route("/", methods=["GET"])
def index():
    return render_template_string(PAGE, f=_default_form(), error=None, plans=None, summary=None, show_detail=False)


@app.route("/run", methods=["POST"])
def run():
    f = _read_form(request.form)
    action = request.form.get("action")
    mem_cap = None if f["max_memory"] <= 0 else f["max_memory"]

    try:
        if action == "preview":
            plans = preview_playlist(
                f["playlist"], f["track"], f["all_tracks"], f["bars"],
                f["max_hot"], mem_cap, f["skip_seconds"], f["hot_position"],
            )
            for p in plans:
                p.detail_lines = _detail_lines(p) if not f["all_tracks"] else []
            summary = f"Preview — {len(plans)} track(s)"
            show_detail = not f["all_tracks"]
        else:
            result = apply_playlist(
                f["playlist"], f["track"], f["all_tracks"], f["bars"],
                f["max_hot"], mem_cap, f["skip_seconds"], f["hot_position"],
                dry_run=False, overwrite=f["overwrite"],
            )
            plans = result["plans"]
            for p in plans:
                p.detail_lines = []
            backup_note = f" — backup: {result['backup_path']}" if result["backup_path"] else ""
            summary = f"Done: {result['written']} written, {result['total_cues']} cues, {result['skipped']} skipped{backup_note}"
            show_detail = False
    except EvencuesError as e:
        return render_template_string(PAGE, f=f, error=str(e), plans=None, summary=None, show_detail=False)
    except Exception as e:  # noqa: BLE001 — show the user something rather than a bare 500
        return render_template_string(PAGE, f=f, error=f"Unexpected error: {e}", plans=None, summary=None, show_detail=False)

    return render_template_string(PAGE, f=f, error=None, plans=plans, summary=summary, show_detail=show_detail)


def main():
    print("evencues web interface running at http://127.0.0.1:5151")
    app.run(host="127.0.0.1", port=5151, debug=False)


if __name__ == "__main__":
    main()
