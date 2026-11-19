from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from app.main import DATA_ROOT, MEDIA_ROOT, connect, refresh_title_poster


def run(command: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)


def claim() -> dict | None:
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT id,source_path FROM episodes
               WHERE status='ready' AND poster_state='pending' AND lower(source_path) LIKE '%.mp4'
               ORDER BY id LIMIT 1"""
        ).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE episodes SET poster_state='running' WHERE id=?", (row["id"],))
        return dict(row)


def process(episode: dict) -> None:
    episode_id = int(episode["id"])
    source = (MEDIA_ROOT / episode["source_path"]).resolve(strict=True)
    if not source.is_relative_to(MEDIA_ROOT) or not source.is_file():
        raise RuntimeError("سورس دیگر در مسیر تنظیم‌شده پیدا نمی‌شود.")

    probe = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
        "-of", "json", str(source),
    ], timeout=120)
    info = json.loads(probe.stdout)
    duration = float((info.get("format") or {}).get("duration") or 0)
    if not any(s.get("codec_type") == "video" for s in info.get("streams", [])):
        raise RuntimeError("فایل، جریان ویدیویی ندارد.")

    poster_rel = f"posters/episode-{episode_id}.jpg"
    poster = DATA_ROOT / poster_rel
    poster.parent.mkdir(parents=True, exist_ok=True)
    temp_poster = poster.with_name(f"episode-{episode_id}.pending.jpg")
    seek = max(1.0, min(duration * 0.12, max(duration - 1.0, 1.0)))
    try:
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{seek:.3f}",
             "-i", str(source), "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2",
             "-q:v", "3", str(temp_poster)], timeout=180)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "0",
             "-i", str(source), "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2",
             "-q:v", "3", str(temp_poster)], timeout=180)
    if not temp_poster.is_file() or temp_poster.stat().st_size == 0:
        raise RuntimeError("ساخت فریم از سورس انجام نشد.")
    temp_poster.replace(poster)

    with connect() as conn:
        conn.execute(
            "UPDATE episodes SET duration=?,poster_path=?,poster_state='done',error=NULL WHERE id=?",
            (duration, poster_rel, episode_id),
        )
        title_id = conn.execute("SELECT title_id FROM episodes WHERE id=?", (episode_id,)).fetchone()[0]
        refresh_title_poster(conn, int(title_id))


def fail(episode: dict, exc: Exception) -> None:
    message = (str(exc) or exc.__class__.__name__).strip()[-500:]
    (DATA_ROOT / "posters" / f"episode-{episode['id']}.pending.jpg").unlink(missing_ok=True)
    with connect() as conn:
        conn.execute(
            "UPDATE episodes SET poster_state='error',error=? WHERE id=?",
            (f"poster: {message}", episode["id"]),
        )
    print(f"poster episode {episode['id']} failed: {message}", flush=True)


def main() -> None:
    with connect() as conn:
        conn.execute("UPDATE episodes SET poster_state='pending' WHERE poster_state='running'")
    print("Loyt poster worker started", flush=True)
    while True:
        episode = claim()
        if not episode:
            time.sleep(3)
            continue
        try:
            process(episode)
            print(f"poster episode {episode['id']} completed", flush=True)
        except Exception as exc:
            fail(episode, exc)


if __name__ == "__main__":
    main()
