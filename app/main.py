from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import yaml
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field


MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", "/media")).resolve()
DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data")).resolve()
APP_DATA_HOST_PATH = os.getenv("APP_DATA_HOST_PATH", "./data").rstrip("/")
DB_PATH = DATA_ROOT / "loyt.sqlite3"
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SESSION_COOKIE = "loyt_admin"
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".mov", ".avi", ".webm", ".ts", ".mpg", ".mpeg",
                    ".wmv", ".flv", ".asf", ".m2ts", ".mts", ".vob", ".ogv", ".3gp"}
MIN_TS_VIDEO_BYTES = 10_000_000

DATA_ROOT.mkdir(parents=True, exist_ok=True)
(DATA_ROOT / "hls").mkdir(exist_ok=True)
(DATA_ROOT / "posters").mkdir(exist_ok=True)
(DATA_ROOT / "manifests").mkdir(exist_ok=True)
(DATA_ROOT / "previews" / "timeline").mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Loyt", docs_url=None, redoc_url=None)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS titles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK(kind IN ('movie','series')),
                name TEXT NOT NULL,
                synopsis TEXT NOT NULL DEFAULT '',
                year INTEGER,
                poster_path TEXT,
                poster_episode_id INTEGER,
                hidden INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS ux_titles_name_kind ON titles(kind, name COLLATE NOCASE);
            CREATE TABLE IF NOT EXISTS seasons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
                number INTEGER NOT NULL,
                manifest_path TEXT NOT NULL,
                section_number INTEGER NOT NULL DEFAULT 1,
                section_name TEXT NOT NULL DEFAULT '',
                display_number INTEGER NOT NULL DEFAULT 1,
                display_name TEXT NOT NULL DEFAULT '',
                UNIQUE(title_id, number)
            );
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
                season_id INTEGER REFERENCES seasons(id) ON DELETE CASCADE,
                number INTEGER NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL,
                duration REAL,
                status TEXT NOT NULL DEFAULT 'queued',
                poster_path TEXT,
                poster_state TEXT NOT NULL DEFAULT 'pending',
                hidden INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(title_id, season_id, number)
            );
            CREATE INDEX IF NOT EXISTS ix_episodes_status ON episodes(status);
            CREATE TABLE IF NOT EXISTS media_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
                state TEXT NOT NULL DEFAULT 'queued',
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_jobs_state_id ON media_jobs(state, id);
            """
        )
        def ensure_column(table: str, column: str, definition: str) -> bool:
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column in columns:
                return False
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                return True
            except sqlite3.OperationalError as exc:
                if "duplicate column name" in str(exc).lower():
                    return False
                raise

        ensure_column("episodes", "poster_state", "TEXT NOT NULL DEFAULT 'pending'")
        ensure_column("titles", "poster_episode_id", "INTEGER")
        for column, definition in (("hidden", "INTEGER NOT NULL DEFAULT 0"), ("deleted_at", "TEXT")):
            ensure_column("titles", column, definition)
            ensure_column("episodes", column, definition)
        added_display_number = ensure_column("seasons", "display_number", "INTEGER NOT NULL DEFAULT 1")
        for column, definition in (
            ("section_number", "INTEGER NOT NULL DEFAULT 1"),
            ("section_name", "TEXT NOT NULL DEFAULT ''"),
            ("display_name", "TEXT NOT NULL DEFAULT ''"),
        ):
            ensure_column("seasons", column, definition)
        if added_display_number:
            conn.execute("UPDATE seasons SET display_number=number")


init_db()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_media_path(raw: str, must_exist: bool = True, directory: bool = False) -> tuple[Path, str]:
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(422, "مسیر فایل را وارد کنید.")
    candidate = Path(raw.strip())
    if not candidate.is_absolute():
        raise HTTPException(422, "مسیر باید از /media شروع شود.")
    try:
        resolved = candidate.resolve(strict=must_exist)
        rel = resolved.relative_to(MEDIA_ROOT)
    except (ValueError, OSError, RuntimeError):
        raise HTTPException(422, "فایل باید داخل پوشه رسانه‌ای باشد که به /media وصل شده است.")
    if must_exist and not resolved.exists():
        raise HTTPException(422, "فایل یا پوشه پیدا نشد.")
    if directory and not resolved.is_dir():
        raise HTTPException(422, "این مسیر پوشه نیست.")
    if not directory and must_exist and (not resolved.is_file() or resolved.suffix.lower() not in VIDEO_EXTENSIONS):
        raise HTTPException(422, "این فایل ویدیویی پشتیبانی‌شده نیست.")
    return resolved, rel.as_posix()


def is_video_file(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
        return False
    # The library also contains small TypeScript source files ending in .ts.
    return path.suffix.lower() != ".ts" or path.stat().st_size >= MIN_TS_VIDEO_BYTES


def sign_payload(payload: str) -> str:
    if len(SESSION_SECRET) < 32:
        raise HTTPException(503, "کلید نشست روی سرور تنظیم نشده است.")
    mac = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def is_admin(request: Request) -> bool:
    raw = request.cookies.get(SESSION_COOKIE, "")
    try:
        payload, signature = raw.rsplit(".", 1)
        if not hmac.compare_digest(signature, sign_payload(payload)):
            return False
        role, expires = payload.split("|", 1)
        return role == "admin" and int(expires) > int(time.time())
    except (ValueError, HTTPException):
        return False


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(401, "برای این کار وارد پنل مدیر شوید.")


def signed_play_token(episode_id: int, expiry: int) -> str:
    payload = f"play|{episode_id}|{expiry}"
    return f"{expiry}.{sign_payload(payload)}"


def verify_play_token(episode_id: int, token: str) -> bool:
    try:
        expiry_raw, signature = token.split(".", 1)
        expiry = int(expiry_raw)
        if expiry < int(time.time()) or expiry > int(time.time()) + 7200:
            return False
        payload = f"play|{episode_id}|{expiry}"
        return hmac.compare_digest(signature, sign_payload(payload))
    except (ValueError, HTTPException):
        return False


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=500)


class MovieBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    synopsis: str = Field(default="", max_length=3000)
    year: int | None = Field(default=None, ge=1880, le=2200)
    source: str = Field(min_length=1, max_length=1000)


class SeriesBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    synopsis: str = Field(default="", max_length=3000)
    year: int | None = Field(default=None, ge=1880, le=2200)
    season: int = Field(ge=1, le=1000)
    manifest: str = Field(min_length=1, max_length=200000)


class FolderPreviewBody(BaseModel):
    folder: str = Field(min_length=1, max_length=1000)
    season: int = Field(default=1, ge=1, le=1000)


class FolderCommitBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    synopsis: str = Field(default="", max_length=3000)
    year: int | None = Field(default=None, ge=1880, le=2200)
    season: int = Field(ge=1, le=1000)
    folder: str = Field(min_length=1, max_length=1000)


class SourceBody(BaseModel):
    source: str = Field(min_length=1, max_length=1000)


class TitleUpdateBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["movie", "series"]
    synopsis: str = Field(default="", max_length=3000)
    year: int | None = Field(default=None, ge=1880, le=2200)
    poster_episode_id: int | None = Field(default=None, ge=1)


class SeasonUpdateBody(BaseModel):
    display_number: int = Field(ge=1, le=1000)
    display_name: str = Field(default="", max_length=200)
    section_number: int = Field(ge=1, le=1000)
    section_name: str = Field(default="", max_length=200)


class EpisodeUpdateBody(BaseModel):
    number: int = Field(ge=1, le=10000)
    name: str = Field(default="", max_length=200)
    source: str = Field(min_length=1, max_length=1000)
    season_id: int | None = Field(default=None, ge=1)


def create_job(conn: sqlite3.Connection, episode_id: int) -> int:
    episode = conn.execute("SELECT source_path FROM episodes WHERE id=?", (episode_id,)).fetchone()
    if episode and Path(episode["source_path"]).suffix.lower() == ".mp4":
        conn.execute("UPDATE episodes SET status='ready',poster_state='pending',error=NULL WHERE id=?", (episode_id,))
        return 0
    timestamp = now()
    conn.execute("UPDATE episodes SET status='queued', error=NULL WHERE id=?", (episode_id,))
    cur = conn.execute(
        "INSERT INTO media_jobs(episode_id,state,created_at,updated_at) VALUES(?,?,?,?)",
        (episode_id, "queued", timestamp, timestamp),
    )
    return int(cur.lastrowid)


def public_title(row: sqlite3.Row, conn: sqlite3.Connection) -> dict[str, Any]:
    counts = conn.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN status='ready' THEN 1 ELSE 0 END) AS ready
           FROM episodes WHERE title_id=? AND hidden=0 AND deleted_at IS NULL""", (row["id"],)
    ).fetchone()
    total = counts["total"] or 0
    ready = counts["ready"] or 0
    return {
        "id": row["id"], "kind": row["kind"], "name": row["name"],
        "synopsis": row["synopsis"], "year": row["year"],
        "poster": f"/api/poster/title/{row['id']}" if row["poster_path"] else None,
        "episode_count": total,
        "ready_count": ready,
        "pending_count": total - ready,
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/admin/login")
def login(body: LoginBody, response: Response) -> dict[str, bool]:
    if len(ADMIN_PASSWORD) < 12 or len(SESSION_SECRET) < 32 or ADMIN_PASSWORD.startswith("replace-this"):
        raise HTTPException(503, "رمز مدیر و کلید نشست را در تنظیمات Docker وارد کنید.")
    if not hmac.compare_digest(body.password.encode(), ADMIN_PASSWORD.encode()):
        raise HTTPException(401, "رمز واردشده درست نیست.")
    expires = int(time.time()) + 60 * 60 * 12
    payload = f"admin|{expires}"
    response.set_cookie(
        SESSION_COOKIE, f"{payload}.{sign_payload(payload)}", httponly=True,
        secure=os.getenv("COOKIE_SECURE", "0") == "1", samesite="lax", max_age=60 * 60 * 12,
    )
    return {"ok": True}


@app.post("/api/admin/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/admin/session")
def session(request: Request) -> dict[str, bool]:
    return {"authenticated": is_admin(request)}


@app.post("/api/admin/source/check")
def check_source(body: SourceBody, request: Request) -> dict[str, Any]:
    require_admin(request)
    source, _ = safe_media_path(body.source)
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height",
             "-of", "json", str(source)], check=True, capture_output=True, text=True, timeout=45,
        )
        info = json.loads(probe.stdout)
        duration = float((info.get("format") or {}).get("duration") or 0)
        video = next((stream for stream in info.get("streams", []) if stream.get("codec_type") == "video"), None)
        if video is None:
            raise HTTPException(422, "این فایل جریان ویدیویی ندارد.")
        preview = DATA_ROOT / "previews" / f"{secrets.token_hex(12)}.jpg"
        preview.parent.mkdir(parents=True, exist_ok=True)
        seek = max(1.0, min(duration * 0.12, max(duration - 1.0, 1.0)))
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{seek:.3f}", "-i", str(source),
             "-frames:v", "1", "-vf", "scale='min(640,iw)':-2", "-q:v", "5", str(preview)],
            check=True, capture_output=True, text=True, timeout=60,
        )
        encoded = base64.b64encode(preview.read_bytes()).decode("ascii")
        preview.unlink(missing_ok=True)
        return {"duration": duration, "codec": video.get("codec_name", "نامشخص"),
                "resolution": f"{video.get('width', '?')}×{video.get('height', '?')}",
                "preview": f"data:image/jpeg;base64,{encoded}"}
    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        if "preview" in locals():
            preview.unlink(missing_ok=True)
        raise HTTPException(422, "بررسی سورس بیش از زمان مجاز طول کشید.")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        if "preview" in locals():
            preview.unlink(missing_ok=True)
        raise HTTPException(422, f"فایل خوانده نشد: {detail[-240:] or 'قالب یا کدک پشتیبانی‌نشده'}")
    except (OSError, ValueError, json.JSONDecodeError):
        if "preview" in locals():
            preview.unlink(missing_ok=True)
        raise HTTPException(422, "بررسی فایل انجام نشد؛ مسیر و دسترسی سورس را بررسی کنید.")


@app.get("/api/catalog")
def catalog(q: str = "", kind: str = "all") -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT t.* FROM titles t WHERE
               t.hidden=0 AND t.deleted_at IS NULL
               AND EXISTS(SELECT 1 FROM episodes e WHERE e.title_id=t.id
                          AND e.hidden=0 AND e.deleted_at IS NULL)
               AND (?='all' OR t.kind=?) AND (?='' OR lower(t.name) LIKE lower(?))
               ORDER BY t.created_at DESC""",
            (kind, kind, q.strip(), f"%{q.strip()}%"),
        ).fetchall()
        return {"items": [public_title(row, conn) for row in rows]}


@app.get("/api/search")
def search_catalog(q: str = "") -> dict[str, Any]:
    term = q.strip()[:120]
    if len(term) < 2:
        return {"titles": [], "episodes": []}
    escaped_term = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped_term}%"
    prefix_pattern = f"{escaped_term}%"
    with connect() as conn:
        title_rows = conn.execute(
            """SELECT t.* FROM titles t WHERE t.hidden=0 AND t.deleted_at IS NULL
               AND EXISTS(SELECT 1 FROM episodes e WHERE e.title_id=t.id
                          AND e.hidden=0 AND e.deleted_at IS NULL)
               AND (lower(t.name) LIKE lower(?) ESCAPE '\\'
                    OR lower(t.synopsis) LIKE lower(?) ESCAPE '\\')
               ORDER BY CASE WHEN lower(t.name)=lower(?) THEN 0
                             WHEN lower(t.name) LIKE lower(?) ESCAPE '\\' THEN 1 ELSE 2 END,
                        t.name COLLATE NOCASE LIMIT 10""",
            (pattern, pattern, term, prefix_pattern),
        ).fetchall()
        episode_rows = conn.execute(
            """SELECT e.id,e.title_id,e.number,e.name,e.poster_path,
                      t.kind,t.name AS title_name,t.year,t.poster_path AS title_poster,
                      s.display_number AS season_number,s.display_name AS season_name,
                      s.section_number,s.section_name
               FROM episodes e JOIN titles t ON t.id=e.title_id
               LEFT JOIN seasons s ON s.id=e.season_id
               WHERE e.status='ready' AND e.hidden=0 AND e.deleted_at IS NULL
                 AND t.hidden=0 AND t.deleted_at IS NULL
                 AND (lower(e.name) LIKE lower(?) ESCAPE '\\'
                      OR lower(s.display_name) LIKE lower(?) ESCAPE '\\'
                      OR lower(s.section_name) LIKE lower(?) ESCAPE '\\')
               ORDER BY CASE WHEN lower(e.name)=lower(?) THEN 0 ELSE 1 END,
                        t.name COLLATE NOCASE,COALESCE(s.section_number,1),
                        COALESCE(s.display_number,s.number,0),e.number LIMIT 16""",
            (pattern, pattern, pattern, term),
        ).fetchall()
        titles = [public_title(row, conn) for row in title_rows]
        episodes = [{
            "id": row["id"], "title_id": row["title_id"], "kind": row["kind"],
            "title_name": row["title_name"], "year": row["year"],
            "number": row["number"], "name": row["name"],
            "season": row["season_number"] or 1, "season_name": row["season_name"] or "",
            "section": row["section_number"] or 1, "section_name": row["section_name"] or "",
            "poster": f"/api/poster/episode/{row['id']}" if row["poster_path"] else
                      f"/api/poster/title/{row['title_id']}" if row["title_poster"] else None,
        } for row in episode_rows]
        return {"titles": titles, "episodes": episodes}


@app.get("/api/title/{title_id}")
def title_detail(title_id: int) -> dict[str, Any]:
    with connect() as conn:
        title = conn.execute(
            "SELECT * FROM titles WHERE id=? AND hidden=0 AND deleted_at IS NULL", (title_id,)
        ).fetchone()
        if title is None:
            raise HTTPException(404, "عنوان پیدا نشد.")
        seasons = conn.execute(
            """SELECT * FROM seasons s WHERE title_id=? AND EXISTS(
                   SELECT 1 FROM episodes e WHERE e.season_id=s.id
                   AND e.hidden=0 AND e.deleted_at IS NULL AND e.status='ready')
               ORDER BY section_number,display_number,number""", (title_id,)
        ).fetchall()
        eps = conn.execute(
            """SELECT e.*,s.id AS actual_season_id,s.number AS season_sequence,
                      s.display_number AS season_number,s.display_name AS season_name,
                      s.section_number,s.section_name FROM episodes e
               LEFT JOIN seasons s ON s.id=e.season_id
               WHERE e.title_id=? AND e.status='ready' AND e.hidden=0 AND e.deleted_at IS NULL
               ORDER BY COALESCE(s.section_number,1),COALESCE(s.display_number,s.number,0),e.number""", (title_id,)
        ).fetchall()
        return {
            "title": public_title(title, conn),
            "seasons": [{"id": s["id"], "number": s["display_number"],
                         "section": s["section_number"], "section_name": s["section_name"],
                         "name": s["display_name"]} for s in seasons],
            "episodes": [
                {"id": e["id"], "number": e["number"], "name": e["name"],
                 "season_id": e["actual_season_id"], "season": e["season_number"] or 1,
                 "season_name": e["season_name"] or "", "section": e["section_number"] or 1,
                 "section_name": e["section_name"] or "", "duration": e["duration"],
                 "poster": f"/api/poster/episode/{e['id']}" if e["poster_path"] else None}
                for e in eps
            ],
        }


@app.get("/api/poster/title/{title_id}")
def title_poster(title_id: int, request: Request) -> FileResponse:
    with connect() as conn:
        row = conn.execute(
            "SELECT poster_path,hidden,deleted_at FROM titles WHERE id=?", (title_id,)
        ).fetchone()
    if row and (row["hidden"] or row["deleted_at"]) and not is_admin(request):
        raise HTTPException(404, "تصویر پیدا نشد.")
    if row is None or not row["poster_path"]:
        raise HTTPException(404, "تصویر آماده نیست.")
    path = (DATA_ROOT / row["poster_path"]).resolve()
    if not path.is_relative_to(DATA_ROOT) or not path.is_file():
        raise HTTPException(404, "تصویر پیدا نشد.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@app.get("/api/poster/episode/{episode_id}")
def episode_poster(episode_id: int, request: Request) -> FileResponse:
    with connect() as conn:
        row = conn.execute(
            """SELECT e.poster_path,e.hidden,e.deleted_at,t.hidden AS title_hidden,t.deleted_at AS title_deleted
               FROM episodes e JOIN titles t ON t.id=e.title_id WHERE e.id=?""", (episode_id,)
        ).fetchone()
    if row and (row["hidden"] or row["deleted_at"] or row["title_hidden"] or row["title_deleted"]) and not is_admin(request):
        raise HTTPException(404, "تصویر پیدا نشد.")
    if row is None or not row["poster_path"]:
        raise HTTPException(404, "تصویر آماده نیست.")
    path = (DATA_ROOT / row["poster_path"]).resolve()
    if not path.is_relative_to(DATA_ROOT) or not path.is_file():
        raise HTTPException(404, "تصویر پیدا نشد.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@app.get("/api/play/{episode_id}")
def play(episode_id: int) -> dict[str, str]:
    with connect() as conn:
        row = conn.execute(
            """SELECT e.status,e.source_path FROM episodes e JOIN titles t ON t.id=e.title_id
               WHERE e.id=? AND e.hidden=0 AND e.deleted_at IS NULL
               AND t.hidden=0 AND t.deleted_at IS NULL""", (episode_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "قسمت پیدا نشد.")
    if row["status"] != "ready":
        raise HTTPException(409, "این محتوا هنوز برای پخش آماده نشده است.")
    expiry = int(time.time()) + 60 * 60
    token = signed_play_token(episode_id, expiry)
    if Path(row["source_path"]).suffix.lower() == ".mp4":
        return {"url": f"/api/source/{episode_id}?token={quote(token)}", "mode": "file"}
    return {"url": f"/api/stream/{episode_id}/master.m3u8?token={quote(token)}"}


@app.get("/api/source/{episode_id}")
def source_file(episode_id: int, token: str) -> FileResponse:
    authorized_episode(episode_id, token)
    with connect() as conn:
        row = conn.execute("SELECT source_path FROM episodes WHERE id=?", (episode_id,)).fetchone()
    if row is None or Path(row["source_path"]).suffix.lower() != ".mp4":
        raise HTTPException(404, "سورس مستقیم پیدا نشد.")
    path, _ = safe_media_path(f"/media/{row['source_path']}")
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, no-store"})


@app.get("/api/preview/{episode_id}")
def timeline_preview(episode_id: int, token: str, at: float = 0) -> FileResponse:
    authorized_episode(episode_id, token)
    if not math.isfinite(at):
        raise HTTPException(422, "زمان پیش‌نمایش معتبر نیست.")
    with connect() as conn:
        row = conn.execute(
            "SELECT source_path,duration FROM episodes WHERE id=? AND status='ready'", (episode_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "قسمت آماده‌ای پیدا نشد.")
    duration = float(row["duration"] or 0)
    requested_at = max(0.0, min(float(at), duration)) if duration > 0 else max(0.0, float(at))
    bucket = int(requested_at // 3)
    seek_at = bucket * 3 + 1
    if duration > 0:
        seek_at = min(seek_at, max(0.0, duration - 0.15))
    source, _ = safe_media_path(f"/media/{row['source_path']}")
    folder = DATA_ROOT / "previews" / "timeline" / str(episode_id)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{bucket}.jpg"
    if not target.is_file():
        temporary = folder / f"{bucket}-{os.getpid()}-{time.time_ns()}.jpg"
        try:
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{seek_at:.3f}",
                 "-i", str(source), "-an", "-frames:v", "1", "-vf",
                 "scale=320:-2:force_original_aspect_ratio=decrease", "-q:v", "6", "-y", str(temporary)],
                capture_output=True, timeout=20,
            )
            if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
                raise HTTPException(503, "فریم پیش‌نمایش این زمان ساخته نشد.")
            os.replace(temporary, target)
        except subprocess.TimeoutExpired:
            raise HTTPException(503, "ساخت پیش‌نمایش بیش از حد طول کشید.")
        except OSError:
            raise HTTPException(503, "ساخت فریم پیش‌نمایش در دسترس نیست.")
        finally:
            temporary.unlink(missing_ok=True)
    return FileResponse(target, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/episode/{episode_id}")
def episode_detail(episode_id: int) -> dict[str, Any]:
    with connect() as conn:
        current = conn.execute(
            """SELECT e.*,s.number AS season_sequence,s.display_number AS season_number,
               s.section_number,s.section_name,s.display_name AS season_name,
               t.kind,t.name AS title_name,t.synopsis,t.year,
               t.poster_path AS title_poster FROM episodes e
               LEFT JOIN seasons s ON s.id=e.season_id JOIN titles t ON t.id=e.title_id
               WHERE e.id=? AND e.status='ready' AND e.hidden=0 AND e.deleted_at IS NULL
                 AND t.hidden=0 AND t.deleted_at IS NULL""", (episode_id,)
        ).fetchone()
        if current is None:
            raise HTTPException(404, "قسمت آماده‌ای با این شناسه پیدا نشد.")
        siblings = conn.execute(
            """SELECT e.id,e.number,e.name,e.poster_path,s.number AS season_sequence,
                      s.display_number AS season_number,s.section_number,s.section_name,
                      s.display_name AS season_name
               FROM episodes e LEFT JOIN seasons s ON s.id=e.season_id
               WHERE e.title_id=? AND e.status='ready' AND e.hidden=0 AND e.deleted_at IS NULL
               ORDER BY COALESCE(s.section_number,1),COALESCE(s.display_number,s.number,0),e.number""",
            (current["title_id"],)
        ).fetchall()
        sequence = [dict(row) for row in siblings]
        index = next(i for i, item in enumerate(sequence) if item["id"] == episode_id)
        next_episode = sequence[index + 1] if index + 1 < len(sequence) else None
        if next_episode:
            next_episode = {
                "id": next_episode["id"], "number": next_episode["number"],
                "name": next_episode["name"], "season": next_episode["season_number"] or 1,
                "section": next_episode["section_number"] or 1,
                "section_name": next_episode["section_name"] or "",
                "season_name": next_episode["season_name"] or "",
                "poster": f"/api/poster/episode/{next_episode['id']}" if next_episode["poster_path"] else None,
            }
        return {
            "title": {"id": current["title_id"], "kind": current["kind"], "name": current["title_name"],
                      "synopsis": current["synopsis"], "year": current["year"],
                      "poster": f"/api/poster/title/{current['title_id']}" if current["title_poster"] else None},
            "episode": {"id": current["id"], "number": current["number"], "name": current["name"],
                        "season": current["season_number"] or 1,"season_name": current["season_name"] or "",
                        "section": current["section_number"] or 1,"section_name": current["section_name"] or "",
                        "duration": current["duration"],
                        "poster": f"/api/poster/episode/{current['id']}" if current["poster_path"] else None},
            "next": next_episode,
        }


def authorized_episode(episode_id: int, token: str) -> None:
    if not verify_play_token(episode_id, token):
        raise HTTPException(403, "پیوند پخش منقضی شده است.")
    with connect() as conn:
        row = conn.execute(
            """SELECT e.status FROM episodes e JOIN titles t ON t.id=e.title_id
               WHERE e.id=? AND e.hidden=0 AND e.deleted_at IS NULL
                 AND t.hidden=0 AND t.deleted_at IS NULL""", (episode_id,)
        ).fetchone()
    if row is None or row["status"] != "ready":
        raise HTTPException(404, "رسانه آماده نیست.")


@app.get("/api/stream/{episode_id}/master.m3u8")
def hls_playlist(episode_id: int, token: str) -> PlainTextResponse:
    authorized_episode(episode_id, token)
    playlist = DATA_ROOT / "hls" / str(episode_id) / "master.m3u8"
    if not playlist.is_file():
        raise HTTPException(404, "فهرست پخش پیدا نشد.")
    content = playlist.read_text(encoding="utf-8")

    def rewrite(match: re.Match[str]) -> str:
        filename = Path(match.group(1)).name
        return f'URI="/api/stream/{episode_id}/{quote(filename)}?token={quote(token)}"'

    content = re.sub(r'URI="([^"]+)"', rewrite, content)
    rewritten: list[str] = []
    for line in content.splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            value = f"/api/stream/{episode_id}/{quote(Path(value).name)}?token={quote(token)}"
        rewritten.append(value)
    return PlainTextResponse("\n".join(rewritten) + "\n", media_type="application/vnd.apple.mpegurl",
                             headers={"Cache-Control": "no-store"})


@app.get("/api/stream/{episode_id}/{filename}")
def hls_file(episode_id: int, filename: str, token: str) -> Response:
    authorized_episode(episode_id, token)
    if filename != Path(filename).name or filename in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
        raise HTTPException(404, "قطعه پیدا نشد.")
    path = DATA_ROOT / "hls" / str(episode_id) / filename
    if not path.is_file():
        raise HTTPException(404, "قطعه پیدا نشد.")
    content_type = "video/mp4" if filename.endswith((".mp4", ".m4s")) else "application/octet-stream"
    if filename.endswith(".m3u8"):
        content_type = "application/vnd.apple.mpegurl"
    response = Response(status_code=200, media_type=content_type)
    response.headers["X-Accel-Redirect"] = f"/_hls/{episode_id}/{quote(filename)}"
    response.headers["Cache-Control"] = "private, max-age=300"
    return response


def parse_manifest(raw: str, requested_name: str, requested_season: int) -> list[dict[str, Any]]:
    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise HTTPException(422, f"YAML قابل خواندن نیست: {exc.__class__.__name__}")
    if not isinstance(doc, dict) or doc.get("series") != requested_name or doc.get("season") != requested_season:
        raise HTTPException(422, "نام سریال و شماره فصل YAML باید با فرم یکی باشد.")
    raw_episodes = doc.get("episodes")
    if not isinstance(raw_episodes, list) or not raw_episodes:
        raise HTTPException(422, "حداقل یک قسمت در YAML لازم است.")
    seen: set[int] = set()
    episodes: list[dict[str, Any]] = []
    for item in raw_episodes:
        if not isinstance(item, dict) or not isinstance(item.get("number"), int) or item["number"] < 1:
            raise HTTPException(422, "هر قسمت باید شماره مثبت داشته باشد.")
        number = item["number"]
        if number in seen:
            raise HTTPException(422, f"شماره قسمت {number} تکراری است.")
        seen.add(number)
        src = item.get("source")
        _, relative = safe_media_path(src)
        episode_name = str(item.get("title", "")).strip()[:200]
        episodes.append({"number": number, "name": episode_name, "source_path": relative})
    return sorted(episodes, key=lambda e: e["number"])


def manifest_text(name: str, season: int, episodes: list[dict[str, Any]]) -> str:
    doc = {"series": name, "season": season, "episodes": [
        {"number": ep["number"], **({"title": ep["name"]} if ep.get("name") else {}),
         "source": f"/media/{ep['source_path']}"} for ep in episodes
    ]}
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=100)


def rewrite_season_manifest(conn: sqlite3.Connection, season_id: int) -> None:
    season = conn.execute(
        """SELECT s.*,t.name AS title_name FROM seasons s
           JOIN titles t ON t.id=s.title_id WHERE s.id=?""", (season_id,)
    ).fetchone()
    if season is None:
        return
    rows = conn.execute(
        "SELECT number,name,source_path FROM episodes WHERE season_id=? ORDER BY number,id", (season_id,)
    ).fetchall()
    relative = Path(season["manifest_path"])
    target = (DATA_ROOT / relative).resolve()
    if not target.is_relative_to(DATA_ROOT):
        raise HTTPException(422, "مسیر فایل فهرست فصل معتبر نیست.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(manifest_text(season["title_name"], season["display_number"], [dict(row) for row in rows]),
                      encoding="utf-8")


def refresh_title_poster(conn: sqlite3.Connection, title_id: int) -> None:
    conn.execute(
        """UPDATE titles SET poster_path=COALESCE(
             (SELECT e.poster_path FROM episodes e
              WHERE e.id=titles.poster_episode_id AND e.title_id=titles.id AND e.status='ready'
                AND e.hidden=0 AND e.deleted_at IS NULL),
             (SELECT e.poster_path FROM episodes e LEFT JOIN seasons s ON s.id=e.season_id
              WHERE e.title_id=titles.id AND e.poster_path IS NOT NULL AND e.status='ready'
                AND e.hidden=0 AND e.deleted_at IS NULL
              ORDER BY COALESCE(s.section_number,1),COALESCE(s.display_number,s.number),e.number LIMIT 1)
           ) WHERE id=?""", (title_id,)
    )


def save_series(body_name: str, synopsis: str, year: int | None, season_number: int,
                episode_items: list[dict[str, Any]], raw_manifest: str) -> dict[str, Any]:
    with connect() as conn:
        title = conn.execute(
            "SELECT * FROM titles WHERE kind='series' AND name=? COLLATE NOCASE", (body_name,)
        ).fetchone()
        if title is None:
            cur = conn.execute("INSERT INTO titles(kind,name,synopsis,year,created_at) VALUES('series',?,?,?,?)",
                               (body_name, synopsis, year, now()))
            title_id = int(cur.lastrowid)
        else:
            title_id = int(title["id"])
            exists = conn.execute("SELECT 1 FROM seasons WHERE title_id=? AND number=?", (title_id, season_number)).fetchone()
            if exists:
                raise HTTPException(409, "این فصل در حال حاضر در کاتالوگ وجود دارد.")
            conn.execute("UPDATE titles SET synopsis=?, year=COALESCE(year,?) WHERE id=?", (synopsis, year, title_id))
        slug = re.sub(r"[^\w-]+", "-", body_name.lower(), flags=re.UNICODE).strip("-")[:50] or "series"
        rel_manifest = f"manifests/{slug}-{title_id}/season-{season_number:02d}.yml"
        manifest_path = DATA_ROOT / rel_manifest
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        cur = conn.execute("""INSERT INTO seasons(title_id,number,manifest_path,display_number)
                              VALUES(?,?,?,?)""", (title_id, season_number, rel_manifest, season_number))
        season_id = int(cur.lastrowid)
        episode_ids: list[int] = []
        for item in episode_items:
            cur = conn.execute(
                """INSERT INTO episodes(title_id,season_id,number,name,source_path,status,created_at)
                   VALUES(?,?,?,?,?,'queued',?)""",
                (title_id, season_id, item["number"], item["name"], item["source_path"], now()),
            )
            episode_id = int(cur.lastrowid)
            create_job(conn, episode_id)
            episode_ids.append(episode_id)
        # Write inside the guarded data tree; a failed write rolls the DB transaction back.
        manifest_path.write_text(raw_manifest, encoding="utf-8")
        return {"title_id": title_id, "season_id": season_id, "episode_ids": episode_ids,
                "manifest": f"/data/{rel_manifest}",
                "host_manifest": f"{APP_DATA_HOST_PATH}/{rel_manifest}"}


@app.post("/api/admin/movie")
def add_movie(body: MovieBody, request: Request) -> dict[str, Any]:
    require_admin(request)
    _, relative = safe_media_path(body.source)
    with connect() as conn:
        existing = conn.execute("SELECT 1 FROM titles WHERE kind='movie' AND name=? COLLATE NOCASE", (body.name.strip(),)).fetchone()
        if existing:
            raise HTTPException(409, "عنوانی با این نام از قبل هست.")
        cur = conn.execute("INSERT INTO titles(kind,name,synopsis,year,created_at) VALUES('movie',?,?,?,?)",
                           (body.name.strip(), body.synopsis.strip(), body.year, now()))
        title_id = int(cur.lastrowid)
        cur = conn.execute("""INSERT INTO episodes(title_id,number,name,source_path,status,created_at)
                            VALUES(?,1,?,?, 'queued',?)""",
                           (title_id, body.name.strip(), relative, now()))
        episode_id = int(cur.lastrowid)
        job_id = create_job(conn, episode_id)
    return {"title_id": title_id, "episode_id": episode_id, "job_id": job_id}


@app.post("/api/admin/series")
def add_series(body: SeriesBody, request: Request) -> dict[str, Any]:
    require_admin(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "نام سریال را وارد کنید.")
    items = parse_manifest(body.manifest, name, body.season)
    normalized = manifest_text(name, body.season, items)
    return save_series(name, body.synopsis.strip(), body.year, body.season, items, normalized)


def natural_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def folder_episodes(folder_path: Path, relative_folder: str, requested_season: int) -> list[dict[str, Any]]:
    files = [p for p in folder_path.iterdir() if is_video_file(p)]
    files.sort(key=lambda p: natural_key(p.name))
    if not files:
        raise HTTPException(422, "در این پوشه فایل ویدیویی پیدا نشد.")
    result: list[dict[str, Any]] = []
    used: set[int] = set()
    for index, path in enumerate(files, start=1):
        match = re.search(r"S(\d{1,3})E(\d{1,4})", path.stem, re.IGNORECASE)
        if match and int(match.group(1)) != requested_season:
            raise HTTPException(422, f"فایل {path.name} برای فصل {int(match.group(1))} است، نه فصل {requested_season}.")
        number = int(match.group(2)) if match else index
        if number in used:
            raise HTTPException(422, f"شماره قسمت {number} در نام فایل‌ها تکرار شده است.")
        used.add(number)
        name = re.sub(r"[._]+", " ", path.stem).strip()
        name = re.sub(r"(?i)S\d{1,3}E\d{1,4}", "", name).strip(" -_")
        result.append({"number": number, "name": name[:200], "source_path": f"{relative_folder}/{path.name}".lstrip("/")})
    return sorted(result, key=lambda e: e["number"])


@app.post("/api/admin/import/preview")
def import_preview(body: FolderPreviewBody, request: Request) -> dict[str, Any]:
    require_admin(request)
    path, rel = safe_media_path(body.folder, directory=True)
    episodes = folder_episodes(path, rel, body.season)
    return {"folder": f"/media/{rel}", "episodes": episodes,
            "manifest_example": manifest_text("نام سریال", body.season, episodes)}


@app.get("/api/admin/browse")
def browse_media(request: Request, path: str = "/media") -> dict[str, Any]:
    require_admin(request)
    folder, rel = safe_media_path(path, directory=True)
    display_path = "/media" if rel == "." else f"/media/{rel}"
    directories: list[dict[str, str]] = []
    video_count = 0
    try:
        children = list(folder.iterdir())
    except PermissionError:
        raise HTTPException(403, "اجازه خواندن این پوشه وجود ندارد.")
    for child in children:
        if child.name.startswith("."):
            continue
        try:
            resolved = child.resolve(strict=True)
            resolved.relative_to(MEDIA_ROOT)
        except (ValueError, OSError, RuntimeError):
            continue
        if resolved.is_dir():
            child_rel = resolved.relative_to(MEDIA_ROOT).as_posix()
            directories.append({"name": child.name, "path": f"/media/{child_rel}"})
        elif is_video_file(resolved):
            video_count += 1
    directories.sort(key=lambda item: natural_key(item["name"]))
    parent = None
    if folder != MEDIA_ROOT:
        parent_path = folder.parent.resolve()
        parent = "/media" if parent_path == MEDIA_ROOT else f"/media/{parent_path.relative_to(MEDIA_ROOT).as_posix()}"
    return {"path": display_path, "parent": parent, "directories": directories, "video_count": video_count}


@app.post("/api/admin/import/commit")
def import_commit(body: FolderCommitBody, request: Request) -> dict[str, Any]:
    require_admin(request)
    folder, rel = safe_media_path(body.folder, directory=True)
    episodes = folder_episodes(folder, rel, body.season)
    normalized = manifest_text(body.name.strip(), body.season, episodes)
    validated = parse_manifest(normalized, body.name.strip(), body.season)
    return save_series(body.name.strip(), body.synopsis.strip(), body.year, body.season, validated, normalized)


@app.get("/api/admin/overview")
def admin_overview(request: Request) -> dict[str, Any]:
    require_admin(request)
    with connect() as conn:
        return {
            "titles": conn.execute("SELECT COUNT(*) FROM titles").fetchone()[0],
            "episodes": conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0],
            "queued": conn.execute("SELECT COUNT(*) FROM media_jobs WHERE state='queued'").fetchone()[0],
            "failed": conn.execute("SELECT COUNT(*) FROM media_jobs WHERE state='error'").fetchone()[0],
        }


@app.get("/api/admin/titles")
def admin_titles(request: Request, status: str = "active", q: str = "") -> dict[str, Any]:
    require_admin(request)
    filters = {
        "active": "t.hidden=0 AND t.deleted_at IS NULL",
        "hidden": "t.hidden=1 AND t.deleted_at IS NULL",
        "deleted": "t.deleted_at IS NOT NULL",
        "all": "1=1",
    }
    if status not in filters:
        raise HTTPException(422, "فیلتر وضعیت عنوان معتبر نیست.")
    with connect() as conn:
        rows = conn.execute(f"""SELECT t.*, COUNT(e.id) AS episode_count,
            SUM(CASE WHEN e.status='ready' THEN 1 ELSE 0 END) AS ready_count
            FROM titles t LEFT JOIN episodes e ON e.title_id=t.id
            WHERE {filters[status]} AND (?='' OR lower(t.name) LIKE lower(?))
            GROUP BY t.id ORDER BY t.created_at DESC""",
            (q.strip(), f"%{q.strip()}%"),
        ).fetchall()
        return {"items": [dict(row) for row in rows]}


@app.get("/api/admin/title/{title_id}")
def admin_title_detail(title_id: int, request: Request) -> dict[str, Any]:
    require_admin(request)
    with connect() as conn:
        title = conn.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
        if title is None:
            raise HTTPException(404, "عنوان پیدا نشد.")
        seasons = conn.execute(
            "SELECT * FROM seasons WHERE title_id=? ORDER BY section_number,display_number,number", (title_id,)
        ).fetchall()
        episodes = conn.execute(
            """SELECT e.*,s.display_number AS season_display_number,s.display_name AS season_display_name,
                      s.section_number,s.section_name
               FROM episodes e LEFT JOIN seasons s ON s.id=e.season_id
               WHERE e.title_id=?
               ORDER BY COALESCE(s.section_number,1),COALESCE(s.display_number,s.number,0),e.number,e.id""",
            (title_id,),
        ).fetchall()
        return {
            "title": dict(title),
            "seasons": [dict(row) for row in seasons],
            "episodes": [dict(row) for row in episodes],
        }


def set_content_state(kind: Literal["title", "episode"], content_id: int, action: str) -> dict[str, bool]:
    table = "titles" if kind == "title" else "episodes"
    with connect() as conn:
        row = conn.execute(f"SELECT id,hidden,deleted_at FROM {table} WHERE id=?", (content_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "محتوا پیدا نشد.")
        if action == "hide":
            if row["deleted_at"]:
                raise HTTPException(409, "ابتدا محتوا را از زباله‌دان بازیابی کنید.")
            conn.execute(f"UPDATE {table} SET hidden=1 WHERE id=?", (content_id,))
        elif action == "show":
            if row["deleted_at"]:
                raise HTTPException(409, "ابتدا محتوا را از زباله‌دان بازیابی کنید.")
            conn.execute(f"UPDATE {table} SET hidden=0 WHERE id=?", (content_id,))
        elif action == "trash":
            conn.execute(f"UPDATE {table} SET deleted_at=? WHERE id=?", (now(), content_id))
        elif action == "restore":
            conn.execute(f"UPDATE {table} SET deleted_at=NULL WHERE id=?", (content_id,))
        else:
            raise HTTPException(422, "عملیات محتوا معتبر نیست.")
        if kind == "episode":
            episode = conn.execute("SELECT title_id FROM episodes WHERE id=?", (content_id,)).fetchone()
            refresh_title_poster(conn, int(episode["title_id"]))
    return {"ok": True}


def safe_data_artifact(relative: str | None) -> Path | None:
    if not relative:
        return None
    path = (DATA_ROOT / relative).resolve()
    return path if path.is_relative_to(DATA_ROOT) and path != DATA_ROOT else None


def remove_generated_artifacts(paths: list[str | None], episode_ids: list[int]) -> None:
    unique_paths = {path for relative in paths if (path := safe_data_artifact(relative)) is not None}
    with connect() as conn:
        for path in unique_paths:
            relative = path.relative_to(DATA_ROOT).as_posix()
            referenced = conn.execute(
                "SELECT 1 FROM episodes WHERE poster_path=? UNION SELECT 1 FROM titles WHERE poster_path=? LIMIT 1",
                (relative, relative),
            ).fetchone()
            if not referenced and path.is_file():
                path.unlink(missing_ok=True)
    for episode_id in episode_ids:
        for relative in (f"hls/{episode_id}", f"previews/timeline/{episode_id}"):
            path = safe_data_artifact(relative)
            if path and path.is_dir():
                shutil.rmtree(path)


@app.post("/api/admin/title/{title_id}/hide")
def hide_title(title_id: int, request: Request) -> dict[str, bool]:
    require_admin(request)
    return set_content_state("title", title_id, "hide")


@app.post("/api/admin/title/{title_id}/show")
def show_title(title_id: int, request: Request) -> dict[str, bool]:
    require_admin(request)
    return set_content_state("title", title_id, "show")


@app.post("/api/admin/title/{title_id}/trash")
def trash_title(title_id: int, request: Request) -> dict[str, bool]:
    require_admin(request)
    return set_content_state("title", title_id, "trash")


@app.post("/api/admin/title/{title_id}/restore")
def restore_title(title_id: int, request: Request) -> dict[str, bool]:
    require_admin(request)
    return set_content_state("title", title_id, "restore")


@app.delete("/api/admin/title/{title_id}")
def permanently_delete_title(title_id: int, request: Request) -> dict[str, bool]:
    require_admin(request)
    generated: list[str | None] = []
    episode_ids: list[int] = []
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        title = conn.execute("SELECT deleted_at FROM titles WHERE id=?", (title_id,)).fetchone()
        if title is None:
            raise HTTPException(404, "عنوان پیدا نشد.")
        if not title["deleted_at"]:
            raise HTTPException(409, "برای حذف دائمی، ابتدا عنوان را به زباله‌دان منتقل کنید.")
        busy = conn.execute(
            """SELECT 1 FROM episodes e LEFT JOIN media_jobs j ON j.episode_id=e.id
               WHERE e.title_id=? AND (e.status='processing' OR e.poster_state='running' OR j.state='running') LIMIT 1""",
            (title_id,),
        ).fetchone()
        if busy:
            raise HTTPException(409, "آماده‌سازی یکی از قسمت‌ها هنوز در حال اجراست؛ کمی بعد دوباره تلاش کنید.")
        episode_rows = conn.execute(
            "SELECT id,poster_path FROM episodes WHERE title_id=?", (title_id,)
        ).fetchall()
        episode_ids = [int(row["id"]) for row in episode_rows]
        generated.extend(row["poster_path"] for row in episode_rows)
        generated.extend(row["manifest_path"] for row in conn.execute(
            "SELECT manifest_path FROM seasons WHERE title_id=?", (title_id,)
        ).fetchall())
        generated.extend(row["poster_path"] for row in conn.execute(
            "SELECT poster_path FROM titles WHERE id=?", (title_id,)
        ).fetchall())
        conn.execute("DELETE FROM titles WHERE id=?", (title_id,))
    remove_generated_artifacts(generated, episode_ids)
    return {"ok": True}


@app.post("/api/admin/episode/{episode_id}/hide")
def hide_episode(episode_id: int, request: Request) -> dict[str, bool]:
    require_admin(request)
    return set_content_state("episode", episode_id, "hide")


@app.post("/api/admin/episode/{episode_id}/show")
def show_episode(episode_id: int, request: Request) -> dict[str, bool]:
    require_admin(request)
    return set_content_state("episode", episode_id, "show")


@app.post("/api/admin/episode/{episode_id}/trash")
def trash_episode(episode_id: int, request: Request) -> dict[str, bool]:
    require_admin(request)
    return set_content_state("episode", episode_id, "trash")


@app.post("/api/admin/episode/{episode_id}/restore")
def restore_episode(episode_id: int, request: Request) -> dict[str, bool]:
    require_admin(request)
    return set_content_state("episode", episode_id, "restore")


@app.delete("/api/admin/episode/{episode_id}")
def permanently_delete_episode(episode_id: int, request: Request) -> dict[str, bool]:
    require_admin(request)
    generated: list[str | None] = []
    title_id: int
    season_id: int | None
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        episode = conn.execute(
            "SELECT * FROM episodes WHERE id=?", (episode_id,)
        ).fetchone()
        if episode is None:
            raise HTTPException(404, "قسمت پیدا نشد.")
        if not episode["deleted_at"]:
            raise HTTPException(409, "برای حذف دائمی، ابتدا قسمت را به زباله‌دان منتقل کنید.")
        busy = conn.execute(
            """SELECT 1 FROM episodes e LEFT JOIN media_jobs j ON j.episode_id=e.id
               WHERE e.id=? AND (e.status='processing' OR e.poster_state='running' OR j.state='running') LIMIT 1""",
            (episode_id,),
        ).fetchone()
        if busy:
            raise HTTPException(409, "آماده‌سازی این قسمت هنوز در حال اجراست؛ کمی بعد دوباره تلاش کنید.")
        title_id = int(episode["title_id"])
        season_id = int(episode["season_id"]) if episode["season_id"] is not None else None
        generated.append(episode["poster_path"])
        conn.execute("UPDATE titles SET poster_episode_id=NULL WHERE id=? AND poster_episode_id=?", (title_id, episode_id))
        conn.execute("DELETE FROM episodes WHERE id=?", (episode_id,))
        refresh_title_poster(conn, title_id)
        if season_id is not None:
            rewrite_season_manifest(conn, season_id)
    remove_generated_artifacts(generated, [episode_id])
    return {"ok": True}


@app.patch("/api/admin/title/{title_id}")
def update_title(title_id: int, body: TitleUpdateBody, request: Request) -> dict[str, bool]:
    require_admin(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "نام عنوان را وارد کنید.")
    with connect() as conn:
        title = conn.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
        if title is None:
            raise HTTPException(404, "عنوان پیدا نشد.")
        duplicate = conn.execute(
            "SELECT id FROM titles WHERE id<>? AND kind=? AND name=? COLLATE NOCASE",
            (title_id, body.kind, name),
        ).fetchone()
        if duplicate:
            raise HTTPException(409, "عنوان دیگری با همین نام و نوع وجود دارد.")
        episodes = conn.execute("SELECT id,season_id FROM episodes WHERE title_id=? ORDER BY id", (title_id,)).fetchall()
        if body.kind == "movie" and len(episodes) > 1:
            raise HTTPException(409, "این عنوان چند قسمت دارد؛ برای تبدیل به فیلم ابتدا قسمت‌های اضافه را از محتوای دیگری جدا کنید.")
        if body.poster_episode_id is not None:
            selected = conn.execute(
                "SELECT id FROM episodes WHERE id=? AND title_id=?", (body.poster_episode_id, title_id)
            ).fetchone()
            if selected is None:
                raise HTTPException(422, "فریم انتخابی باید از یکی از قسمت‌های همین عنوان باشد.")

        if title["kind"] == "series" and body.kind == "movie":
            conn.execute("UPDATE episodes SET season_id=NULL WHERE title_id=?", (title_id,))
            conn.execute("DELETE FROM seasons WHERE title_id=?", (title_id,))
        elif title["kind"] == "movie" and body.kind == "series":
            next_number = int(conn.execute(
                "SELECT COALESCE(MAX(number),0)+1 FROM seasons WHERE title_id=?", (title_id,)
            ).fetchone()[0])
            slug = re.sub(r"[^\w-]+", "-", name.lower(), flags=re.UNICODE).strip("-")[:50] or "series"
            relative = f"manifests/{slug}-{title_id}/season-{next_number:02d}.yml"
            cur = conn.execute(
                "INSERT INTO seasons(title_id,number,manifest_path,display_number) VALUES(?,?,?,?)",
                (title_id, next_number, relative, next_number),
            )
            conn.execute("UPDATE episodes SET season_id=? WHERE title_id=?", (cur.lastrowid, title_id))

        conn.execute(
            "UPDATE titles SET kind=?,name=?,synopsis=?,year=?,poster_episode_id=? WHERE id=?",
            (body.kind, name, body.synopsis.strip(), body.year, body.poster_episode_id, title_id),
        )
        refresh_title_poster(conn, title_id)
        for season in conn.execute("SELECT id FROM seasons WHERE title_id=?", (title_id,)).fetchall():
            rewrite_season_manifest(conn, int(season["id"]))
    return {"ok": True}


@app.patch("/api/admin/season/{season_id}")
def update_season(season_id: int, body: SeasonUpdateBody, request: Request) -> dict[str, bool]:
    require_admin(request)
    with connect() as conn:
        season = conn.execute("SELECT id,title_id FROM seasons WHERE id=?", (season_id,)).fetchone()
        if season is None:
            raise HTTPException(404, "فصل پیدا نشد.")
        conn.execute(
            """UPDATE seasons SET display_number=?,display_name=?,section_number=?,section_name=?
               WHERE id=?""",
            (body.display_number, body.display_name.strip(), body.section_number,
             body.section_name.strip(), season_id),
        )
        rewrite_season_manifest(conn, season_id)
        refresh_title_poster(conn, int(season["title_id"]))
    return {"ok": True}


@app.patch("/api/admin/episode/{episode_id}")
def update_episode(episode_id: int, body: EpisodeUpdateBody, request: Request) -> dict[str, bool]:
    require_admin(request)
    _, relative_source = safe_media_path(body.source)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        episode = conn.execute(
            "SELECT * FROM episodes WHERE id=?", (episode_id,)
        ).fetchone()
        if episode is None:
            raise HTTPException(404, "قسمت پیدا نشد.")
        title = conn.execute("SELECT kind FROM titles WHERE id=?", (episode["title_id"],)).fetchone()
        if title is None:
            raise HTTPException(404, "عنوان این قسمت پیدا نشد.")
        if title["kind"] == "movie" and body.season_id is not None:
            raise HTTPException(422, "فیلم نمی‌تواند به فصل وصل باشد.")
        if body.season_id is not None:
            season = conn.execute(
                "SELECT id FROM seasons WHERE id=? AND title_id=?", (body.season_id, episode["title_id"])
            ).fetchone()
            if season is None:
                raise HTTPException(422, "فصل انتخابی متعلق به همین عنوان نیست.")
        collision = conn.execute(
            """SELECT id FROM episodes WHERE title_id=? AND id<>? AND number=?
               AND COALESCE(season_id,0)=COALESCE(?,0) LIMIT 1""",
            (episode["title_id"], episode_id, body.number, body.season_id),
        ).fetchone()
        if collision:
            raise HTTPException(409, "این شماره قسمت در فصل انتخاب‌شده قبلاً استفاده شده است.")

        source_changed = relative_source != episode["source_path"]
        if source_changed:
            if episode["status"] == "processing" or episode["poster_state"] == "running":
                raise HTTPException(409, "آماده‌سازی این قسمت در حال اجراست؛ کمی بعد مسیر سورس را تغییر بده.")
            running_job = conn.execute(
                "SELECT 1 FROM media_jobs WHERE episode_id=? AND state='running' LIMIT 1", (episode_id,)
            ).fetchone()
            if running_job:
                raise HTTPException(409, "آماده‌سازی این قسمت در حال اجراست؛ کمی بعد مسیر سورس را تغییر بده.")
            conn.execute(
                """UPDATE episodes SET title_id=?,season_id=?,number=?,name=?,source_path=?,duration=NULL,
                   poster_path=NULL,status='queued',poster_state='pending',error=NULL WHERE id=?""",
                (episode["title_id"], body.season_id, body.number, body.name.strip(), relative_source, episode_id),
            )
            conn.execute(
                "UPDATE media_jobs SET state='superseded',updated_at=? WHERE episode_id=? AND state<>'running'",
                (now(), episode_id),
            )
            create_job(conn, episode_id)
        else:
            conn.execute(
                "UPDATE episodes SET season_id=?,number=?,name=? WHERE id=?",
                (body.season_id, body.number, body.name.strip(), episode_id),
            )

        for season_id in {episode["season_id"], body.season_id} - {None}:
            rewrite_season_manifest(conn, int(season_id))
        refresh_title_poster(conn, int(episode["title_id"]))
    return {"ok": True, "source_changed": source_changed, "source": f"/media/{relative_source}"}


@app.post("/api/admin/episode/{episode_id}/refresh")
def refresh_episode(episode_id: int, request: Request) -> dict[str, bool]:
    require_admin(request)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        episode = conn.execute(
            "SELECT title_id,status,poster_state FROM episodes WHERE id=?", (episode_id,)
        ).fetchone()
        if episode is None:
            raise HTTPException(404, "قسمت پیدا نشد.")
        running = conn.execute(
            "SELECT 1 FROM media_jobs WHERE episode_id=? AND state='running' LIMIT 1", (episode_id,)
        ).fetchone()
        if episode["status"] == "processing" or episode["poster_state"] == "running" or running:
            raise HTTPException(409, "آماده‌سازی این قسمت همین حالا در حال اجراست.")
        conn.execute(
            "UPDATE episodes SET duration=NULL,poster_path=NULL,status='queued',poster_state='pending',error=NULL WHERE id=?",
            (episode_id,),
        )
        conn.execute(
            "UPDATE media_jobs SET state='superseded',updated_at=? WHERE episode_id=? AND state<>'running'",
            (now(), episode_id),
        )
        create_job(conn, episode_id)
        refresh_title_poster(conn, int(episode["title_id"]))
    return {"ok": True}


@app.get("/api/admin/jobs")
def admin_jobs(request: Request) -> dict[str, Any]:
    require_admin(request)
    with connect() as conn:
        rows = conn.execute("""SELECT j.*, e.number, e.name AS episode_name, e.source_path,
            t.name AS title_name FROM media_jobs j JOIN episodes e ON e.id=j.episode_id
            JOIN titles t ON t.id=e.title_id ORDER BY j.id DESC LIMIT 100""").fetchall()
        return {"items": [dict(row) for row in rows]}


@app.post("/api/admin/jobs/{job_id}/retry")
def retry_job(job_id: int, request: Request) -> dict[str, bool]:
    require_admin(request)
    with connect() as conn:
        row = conn.execute("SELECT episode_id,state FROM media_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "کار پیدا نشد.")
        if row["state"] == "running":
            raise HTTPException(409, "این کار هنوز در حال اجراست.")
        create_job(conn, int(row["episode_id"]))
    return {"ok": True}


@app.get("/{path:path}", response_class=HTMLResponse)
def spa(path: str) -> HTMLResponse:
    if path.startswith("api/") or path.startswith("_hls/"):
        raise HTTPException(404, "مسیر پیدا نشد.")
    index = Path("/web/index.html")
    if not index.is_file():
        return HTMLResponse("Loyt", status_code=200)
    return HTMLResponse(index.read_text(encoding="utf-8"))
