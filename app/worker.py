from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.main import DATA_ROOT, MEDIA_ROOT, connect, now, refresh_title_poster


def claim_job() -> dict | None:
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        stale_before = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds")
        stale_jobs = conn.execute(
            "SELECT id,episode_id FROM media_jobs WHERE state='running' AND updated_at<?",
            (stale_before,),
        ).fetchall()
        for stale in stale_jobs:
            conn.execute(
                "UPDATE media_jobs SET state='queued',error=NULL,updated_at=? WHERE id=? AND state='running'",
                (now(), stale["id"]),
            )
            conn.execute(
                "UPDATE episodes SET status='queued',error=NULL WHERE id=? AND status='processing'",
                (stale["episode_id"],),
            )
        row = conn.execute(
            "SELECT id,episode_id FROM media_jobs WHERE state='queued' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE media_jobs SET state='running',error=NULL,updated_at=? WHERE id=?",
                     (now(), row["id"]))
        conn.execute("UPDATE episodes SET status='processing',error=NULL WHERE id=?", (row["episode_id"],))
        episode = conn.execute(
            """SELECT e.*,t.kind,t.name AS title_name FROM episodes e
               JOIN titles t ON t.id=e.title_id WHERE e.id=?""", (row["episode_id"],)
        ).fetchone()
        return {"job_id": row["id"], "episode": dict(episode)}


def run(command: list[str], timeout: int = 60 * 60) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)


def keep_job_alive(job_id: int, stop: threading.Event) -> None:
    while not stop.wait(30):
        try:
            with connect() as conn:
                conn.execute(
                    "UPDATE media_jobs SET updated_at=? WHERE id=? AND state='running'",
                    (now(), job_id),
                )
        except sqlite3.Error as exc:
            print(f"job {job_id} heartbeat failed: {exc}", flush=True)


def process(job: dict) -> None:
    episode = job["episode"]
    episode_id = int(episode["id"])
    source = (MEDIA_ROOT / episode["source_path"]).resolve(strict=True)
    if not source.is_relative_to(MEDIA_ROOT) or not source.is_file():
        raise RuntimeError("فایل سورس دیگر در مسیر تنظیم‌شده پیدا نمی‌شود.")

    probe = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,pix_fmt",
        "-of", "json", str(source),
    ], timeout=120)
    info = json.loads(probe.stdout)
    duration = float((info.get("format") or {}).get("duration") or 0)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if video is None:
        raise RuntimeError("فایل، جریان ویدیویی ندارد.")

    poster_rel = f"posters/episode-{episode_id}.jpg"
    poster = DATA_ROOT / poster_rel
    poster.parent.mkdir(parents=True, exist_ok=True)
    seek = max(1.0, min(duration * 0.12, max(duration - 1.0, 1.0)))
    try:
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{seek:.3f}",
             "-i", str(source), "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2",
             "-q:v", "3", str(poster)], timeout=180)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "0",
             "-i", str(source), "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2",
             "-q:v", "3", str(poster)], timeout=180)
    if not poster.is_file() or poster.stat().st_size == 0:
        raise RuntimeError("ساخت تصویر از ویدیو انجام نشد.")

    hls_dir = DATA_ROOT / "hls" / str(episode_id)
    if hls_dir.exists():
        shutil.rmtree(hls_dir)
    hls_dir.mkdir(parents=True, exist_ok=True)
    playlist = hls_dir / "master.m3u8"
    video_codec_args = (["-c:v", "copy"] if video.get("codec_name") == "h264" else
                        ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p"])
    audio_codec_args = (["-c:a", "copy"] if not audio or audio.get("codec_name") == "aac" else
                        ["-c:a", "aac", "-b:a", "192k"])
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0?", *video_codec_args, *audio_codec_args,
        "-f", "hls", "-hls_time", "6", "-hls_playlist_type", "vod",
        "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init.mp4",
        "-hls_segment_filename", str(hls_dir / "segment_%05d.m4s"), str(playlist),
    ], timeout=max(60 * 60, int(duration * 2)))
    if not playlist.is_file():
        raise RuntimeError("ساخت فهرست پخش HLS انجام نشد.")

    with connect() as conn:
        conn.execute("UPDATE episodes SET status='ready',duration=?,poster_path=?,error=NULL WHERE id=?",
                     (duration, poster_rel, episode_id))
        conn.execute("UPDATE media_jobs SET state='done',error=NULL,updated_at=? WHERE id=?",
                     (now(), job["job_id"]))
        refresh_title_poster(conn, int(episode["title_id"]))


def fail(job: dict, exc: Exception) -> None:
    message = (str(exc) or exc.__class__.__name__).strip()
    if isinstance(exc, subprocess.CalledProcessError):
        message = (exc.stderr or message).strip()
    message = message[-1200:]
    with connect() as conn:
        conn.execute("UPDATE episodes SET status='error',error=? WHERE id=?",
                     (message, job["episode"]["id"]))
        conn.execute("UPDATE media_jobs SET state='error',error=?,updated_at=? WHERE id=?",
                     (message, now(), job["job_id"]))
    print(f"job {job['job_id']} failed: {message}", flush=True)


def main() -> None:
    print("Loyt media worker started", flush=True)
    while True:
        job = claim_job()
        if not job:
            time.sleep(2)
            continue
        stop_heartbeat = threading.Event()
        heartbeat = threading.Thread(
            target=keep_job_alive, args=(job["job_id"], stop_heartbeat), daemon=True
        )
        heartbeat.start()
        try:
            process(job)
            print(f"job {job['job_id']} completed", flush=True)
        except Exception as exc:
            fail(job, exc)
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=2)


if __name__ == "__main__":
    main()
