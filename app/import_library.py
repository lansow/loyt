from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import yaml

from app.main import DATA_ROOT, MEDIA_ROOT, connect, now, refresh_title_poster
from app.naming import (
    GENERIC_DIRS, clean_episode, clean_title, episode_file_title, folder_label, movie_file_title,
    is_generic_folder, series_folder_title, unique_display_name,
)


SUPPORTED = {".mp4", ".mkv", ".m4v", ".mov", ".avi", ".webm", ".ts", ".mpg", ".mpeg",
             ".wmv", ".flv", ".asf", ".m2ts", ".mts", ".vob", ".ogv", ".3gp"}
DIRECT = {".mp4"}
TS_MIN_BYTES = 10_000_000
NATURAL_PARTS = re.compile(r"(\d+)")
NUMERIC_BRANCH = re.compile(r"^\s*(\d{1,4})(?:\s*[-_. )]|\s*$)")
SECTION_BRANCH = re.compile(r"(?i)^\s*(?:part|section|module|unit|chapter|بخش|قسمت)\s*0*(\d+)(?:\b|\s*[-_. )]|$)")
SEASON_BRANCH = re.compile(r"(?i)^\s*(?:season|s|فصل)\s*0*(\d+)(?:\b|\s*[-_. )]|$)")
EPISODE_SEASON = re.compile(r"(?i)\bS\s*0*(\d{1,3})\s*E\s*0*\d{1,4}\b")


@dataclass
class EpisodeGroup:
    section_number: int
    section_name: str
    season_number: int
    season_name: str
    files: list[Path]


@dataclass
class Candidate:
    name: str
    root: Path
    files: list[Path]
    force_series: bool = False


def import_roots() -> list[Path]:
    configured = os.getenv("IMPORT_ROOTS", "").strip()
    if not configured:
        return [MEDIA_ROOT]

    roots: list[Path] = []
    for value in configured.split(","):
        candidate = Path(value.strip())
        if not candidate.is_absolute():
            candidate = MEDIA_ROOT / candidate
        try:
            root = candidate.resolve(strict=True)
            root.relative_to(MEDIA_ROOT)
        except (OSError, ValueError, RuntimeError) as exc:
            raise ValueError("IMPORT_ROOTS must contain existing folders inside /media.") from exc
        if not root.is_dir():
            raise ValueError("Every IMPORT_ROOTS entry must be a folder.")
        roots.append(root)
    unique = list(dict.fromkeys(roots))
    compact: list[Path] = []
    for root in sorted(unique, key=lambda item: len(item.parts)):
        if not any(root == parent or root.is_relative_to(parent) for parent in compact):
            compact.append(root)
    return compact


def natural_key(value: str) -> list[object]:
    return [int(part) if part.isdigit() else part.casefold() for part in NATURAL_PARTS.split(value)]


def has_video_stream(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        return result.returncode == 0 and result.stdout.strip() == "video"
    except (OSError, subprocess.TimeoutExpired):
        return False


def scan(roots: list[Path]) -> tuple[list[Path], dict[Path, list[Path]], dict[Path, list[Path]], dict[Path, int]]:
    files: list[Path] = []
    direct_files: dict[Path, list[Path]] = defaultdict(list)
    children: dict[Path, list[Path]] = defaultdict(list)
    counts: dict[Path, int] = defaultdict(int)
    seen_paths: set[Path] = set()

    for root in roots:
        if not root.is_dir():
            continue
        for base, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted((name for name in dirs if not name.startswith(".")), key=natural_key)
            folder = Path(base)
            for name in dirs:
                child = folder / name
                children[folder].append(child)
            for name in names:
                path = folder / name
                suffix = path.suffix.lower()
                if suffix not in SUPPORTED:
                    continue
                try:
                    resolved = path.resolve(strict=True)
                    if not resolved.is_relative_to(MEDIA_ROOT) or not resolved.is_file():
                        continue
                    if resolved in seen_paths:
                        continue
                    if suffix == ".ts" and (resolved.stat().st_size < TS_MIN_BYTES or not has_video_stream(resolved)):
                        continue
                except (OSError, RuntimeError):
                    continue
                seen_paths.add(resolved)
                files.append(resolved)
                direct_files[folder].append(resolved)
                ancestor = folder
                while ancestor.is_relative_to(root):
                    counts[ancestor] += 1
                    if ancestor == root:
                        break
                    ancestor = ancestor.parent

    for paths in direct_files.values():
        paths.sort(key=lambda path: natural_key(path.name))
    files.sort(key=lambda path: natural_key(path.as_posix()))
    return files, direct_files, children, counts


def candidate_files(files: list[Path], folder: Path) -> list[Path]:
    return [path for path in files if path.is_relative_to(folder)]


def _section_number(label: str) -> int | None:
    match = SECTION_BRANCH.match(label) or NUMERIC_BRANCH.match(label)
    return int(match.group(1)) if match else None


def _section_label(label: str, number: int) -> str:
    name = folder_label(label)
    return name if name and name != str(number) else f"بخش {number}"


def _season_info(parts: tuple[str, ...]) -> tuple[int, str] | None:
    for part in parts:
        match = SEASON_BRANCH.match(part)
        if match:
            number = int(match.group(1))
            label = folder_label(part)
            return number, label or f"فصل {number}"
    return None


def _explicit_sections(candidate: Candidate) -> list[tuple[str, int, str, list[Path]]]:
    branches: dict[str, list[Path]] = defaultdict(list)
    has_root_files = False
    for path in candidate.files:
        parts = path.relative_to(candidate.root).parts
        if len(parts) == 1:
            has_root_files = True
        else:
            branches[parts[0]].append(path)
    if has_root_files or len(branches) < 2 or not all(_section_number(name) is not None for name in branches):
        return []

    ordered = sorted(branches, key=natural_key)
    output = []
    used_numbers: set[int] = set()
    for index, branch in enumerate(ordered, start=1):
        number = _section_number(branch) or index
        while number in used_numbers:
            number += 1
        used_numbers.add(number)
        output.append((branch, number, _section_label(branch, number), branches[branch]))
    return output


def _groups_in_section(candidate: Candidate, files: list[Path], section_number: int,
                       section_name: str, section_branch: str = "") -> list[EpisodeGroup]:
    season_files: dict[int, list[Path]] = defaultdict(list)
    season_names: dict[int, str] = {}
    unassigned: list[Path] = []
    found_explicit = False
    for path in files:
        parts = path.relative_to(candidate.root).parts[:-1]
        if section_branch and parts and parts[0] == section_branch:
            parts = parts[1:]
        info = _season_info(parts)
        filename_match = EPISODE_SEASON.search(path.stem)
        if filename_match:
            filename_season = int(filename_match.group(1))
            if not info or filename_season != info[0]:
                info = (filename_season, f"فصل {filename_season}")
        if info:
            found_explicit = True
            number, name = info
            season_files[number].append(path)
            if name and (number not in season_names or season_names[number] == f"فصل {number}"):
                season_names[number] = name
        else:
            unassigned.append(path)

    if not found_explicit:
        season_files[1].extend(files)
    elif unassigned:
        # Keep unnumbered files with the only clear season instead of dropping them.
        numbers = sorted(season_files)
        number = numbers[0] if len(numbers) == 1 else 1
        season_files[number].extend(unassigned)

    output: list[EpisodeGroup] = []
    for number, paths in sorted(season_files.items()):
        name = season_names.get(number, "")
        output.append(EpisodeGroup(
            section_number=section_number,
            section_name=section_name,
            season_number=number if found_explicit else 1,
            season_name=name or (f"فصل {number}" if found_explicit else ""),
            files=sorted(paths, key=lambda path: natural_key(path.as_posix())),
        ))
    return output


def season_groups(candidate: Candidate) -> list[EpisodeGroup]:
    sections = _explicit_sections(candidate)
    if sections:
        output: list[EpisodeGroup] = []
        for branch, number, label, files in sections:
            output.extend(_groups_in_section(candidate, files, number, label, branch))
        return output

    # A single wrapper such as Videos/ may enclose several explicit season folders.
    groups = _groups_in_section(candidate, candidate.files, 1, "بخش ۱")
    if len(groups) > 1 or groups[0].season_name:
        return groups
    return [EpisodeGroup(1, "", 1, "", sorted(candidate.files, key=lambda path: natural_key(path.as_posix())))]


def build_candidates(files: list[Path], direct_files: dict[Path, list[Path]],
                     children: dict[Path, list[Path]], counts: dict[Path, int],
                     roots: list[Path]) -> list[Candidate]:
    raw: list[Candidate] = []

    def add_folder(folder: Path, found: list[Path], force_series: bool = False) -> None:
        if found:
            raw.append(Candidate(clean_title(folder.name), folder, found, force_series))

    def add_loose_files(folder: Path) -> None:
        for path in direct_files.get(folder, []):
            raw.append(Candidate(clean_title(clean_episode(path) or folder_label(path.parent.name)),
                                 path.parent, [path]))

    def emit(folder: Path, import_root: Path) -> None:
        direct = direct_files.get(folder, [])
        child_dirs = [child for child in children.get(folder, []) if counts.get(child, 0)]

        if direct:
            if folder == import_root or is_generic_folder(folder.name):
                add_loose_files(folder)
            else:
                add_folder(folder, direct)

        if child_dirs:
            all_files = candidate_files(files, folder)
            explicit_season = any(SEASON_BRANCH.match(child.name) for child in child_dirs)
            explicit_episode = any(EPISODE_SEASON.search(path.stem) for path in all_files)
            if not direct and len(all_files) > 1 and (explicit_season or explicit_episode):
                add_folder(folder, all_files, force_series=True)
                return
            for child in child_dirs:
                emit(child, import_root)

    for root in roots:
        for child in children.get(root, []):
            if counts.get(child, 0):
                emit(child, root)
        if direct_files.get(root):
            add_loose_files(root)

    # Series use their folder name; standalone items use the source filename.
    # Numeric folder and filename labels receive short human-readable context.
    used: dict[str, set[str]] = {"series": set(), "movie": set()}
    for item in raw:
        kind = "series" if item.force_series or len(item.files) > 1 else "movie"
        base = (series_folder_title(item.root, MEDIA_ROOT) if kind == "series"
                else movie_file_title(item.files[0], MEDIA_ROOT))
        item.name = unique_display_name(base, item.root, MEDIA_ROOT, used[kind])
    return raw


def manifest_text(name: str, season: int, episodes: list[dict],
                  section_number: int = 1, section_name: str = "",
                  season_name: str = "") -> str:
    doc = {"series": name, "section": section_number,
           **({"section_name": section_name} if section_name else {}),
           "season": season, **({"season_name": season_name} if season_name else {}),
           "episodes": [
        {"number": item["number"], **({"title": item["name"]} if item["name"] else {}),
         "source": f"/media/{item['source_path']}"} for item in episodes
    ]}
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=100)


def manifest_path(name: str, title_id: int, season: int) -> str:
    slug = re.sub(r"[^\w-]+", "-", name.lower(), flags=re.UNICODE).strip("-")[:50] or "series"
    return f"manifests/{slug}-{title_id}/season-{season:02d}.yml"


def prepare_episode(path: Path, number: int, context: str = "") -> dict:
    source_path = path.relative_to(MEDIA_ROOT).as_posix()
    name = episode_file_title(path, number, MEDIA_ROOT, preferred_context=context)
    return {"number": number, "name": name[:200], "source_path": source_path}


def apply_import(candidates: list[Candidate]) -> tuple[int, int, int, int]:
    manifests: list[tuple[str, str]] = []
    direct_count = 0
    hls_count = 0
    title_count = 0
    episode_count = 0
    timestamp = now()

    with connect() as conn:
        if conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]:
            raise RuntimeError("کتابخانه از قبل محتوا دارد؛ واردسازی گروهی برای جلوگیری از تکرار لغو شد.")
        for candidate in candidates:
            files = sorted(set(candidate.files), key=lambda path: natural_key(path.as_posix()))
            kind = "series" if candidate.force_series or len(files) > 1 else "movie"
            cur = conn.execute(
                "INSERT INTO titles(kind,name,synopsis,year,created_at) VALUES(?,?,?,NULL,?)",
                (kind, candidate.name, "", timestamp),
            )
            title_id = int(cur.lastrowid)
            title_count += 1

            if kind == "movie":
                path = files[0]
                item = prepare_episode(path, 1)
                status = "ready" if path.suffix.lower() in DIRECT else "queued"
                cur = conn.execute(
                    """INSERT INTO episodes(title_id,season_id,number,name,source_path,status,poster_state,created_at)
                       VALUES(?,NULL,1,?,?,?,'pending',?)""",
                    (title_id, item["name"] or candidate.name, item["source_path"], status, timestamp),
                )
                episode_id = int(cur.lastrowid)
                if status == "queued":
                    conn.execute("INSERT INTO media_jobs(episode_id,state,created_at,updated_at) VALUES(?,'queued',?,?)",
                                 (episode_id, timestamp, timestamp))
                    hls_count += 1
                else:
                    direct_count += 1
                episode_count += 1
                continue

            for sequence, group in enumerate(season_groups(candidate), start=1):
                items = []
                for index, path in enumerate(group.files, start=1):
                    items.append(prepare_episode(path, index, group.season_name))
                relative_manifest = manifest_path(candidate.name, title_id, sequence)
                cur = conn.execute(
                    """INSERT INTO seasons(title_id,number,manifest_path,section_number,
                       section_name,display_number,display_name) VALUES(?,?,?,?,?,?,?)""",
                    (title_id, sequence, relative_manifest, group.section_number,
                     group.section_name, group.season_number, group.season_name),
                )
                season_id = int(cur.lastrowid)
                for item, path in zip(items, group.files):
                    status = "ready" if path.suffix.lower() in DIRECT else "queued"
                    cur = conn.execute(
                        """INSERT INTO episodes(title_id,season_id,number,name,source_path,status,poster_state,created_at)
                           VALUES(?,?,?,?,?,?, 'pending',?)""",
                        (title_id, season_id, item["number"], item["name"], item["source_path"], status, timestamp),
                    )
                    episode_id = int(cur.lastrowid)
                    if status == "queued":
                        conn.execute("INSERT INTO media_jobs(episode_id,state,created_at,updated_at) VALUES(?,'queued',?,?)",
                                     (episode_id, timestamp, timestamp))
                        hls_count += 1
                    else:
                        direct_count += 1
                    episode_count += 1
                manifest_items = [dict(number=item["number"], name=item["name"], source_path=item["source_path"])
                                  for item in items]
                manifests.append((relative_manifest, manifest_text(
                    candidate.name, group.season_number, manifest_items, group.section_number,
                    group.section_name, group.season_name)))

        # Files are generated inside /data; the source media mounts remain read-only.
        for relative, content in manifests:
            target = DATA_ROOT / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    return title_count, episode_count, direct_count, hls_count


def _kind(candidate: Candidate) -> str:
    return "series" if candidate.force_series or len(candidate.files) > 1 else "movie"


def sync_import(candidates: list[Candidate], roots: list[Path]) -> tuple[int, int, int, int, int]:
    sources = [path for candidate in candidates for path in candidate.files]
    if len(sources) != len(set(sources)):
        raise RuntimeError("یک فایل بیش از یک بار پیدا شد؛ همگام‌سازی لغو شد.")
    source_paths = {path.relative_to(MEDIA_ROOT).as_posix(): path for path in sources}
    with connect() as conn:
        existing_rows = conn.execute(
            "SELECT id,title_id,source_path,status,poster_path FROM episodes"
        ).fetchall()

    existing = {row["source_path"]: dict(row) for row in existing_rows}
    def is_managed_source(source_path: str) -> bool:
        candidate = (MEDIA_ROOT / source_path).resolve()
        return any(candidate == root or candidate.is_relative_to(root) for root in roots)

    imported_existing = {
        row["source_path"]: row for row in existing.values()
        if is_managed_source(row["source_path"])
    }
    missing = sorted(set(source_paths) - set(existing))
    stale = sorted(set(imported_existing) - set(source_paths))
    new_hls = [source_paths[path] for path in missing if source_paths[path].suffix.lower() not in DIRECT]
    new_hls_bytes = sum(path.stat().st_size for path in new_hls)
    hls_free = shutil.disk_usage(DATA_ROOT / "hls").free
    print(f"sync_existing={len(existing)} discovered={len(source_paths)} new={len(missing)} stale={len(stale)}")
    print(f"new_hls_jobs={len(new_hls)} new_hls_source_gib={new_hls_bytes / 1024**3:.2f} free_hls_gib={hls_free / 1024**3:.2f}")
    if hls_free < new_hls_bytes * 2.2 + 1024**3:
        raise RuntimeError("فضای آزاد HLS برای تبدیل امن فایل‌های تازه کافی نیست؛ هیچ تغییری ذخیره نشد.")

    affected_title_ids = {
        row["title_id"] for row in existing.values()
        if is_managed_source(row["source_path"])
    }
    used_names: dict[str, set[str]] = {"movie": set(), "series": set()}
    with connect() as conn:
        for row in conn.execute("SELECT id,kind,name FROM titles"):
            if row["id"] not in affected_title_ids:
                used_names[row["kind"]].add(row["name"].casefold())

    assigned_title_ids: set[int] = set()
    plan: list[dict] = []
    for candidate in candidates:
        kind = _kind(candidate)
        old_ids = sorted({
            int(existing[path.relative_to(MEDIA_ROOT).as_posix()]["title_id"])
            for path in candidate.files
            if path.relative_to(MEDIA_ROOT).as_posix() in existing
        })
        title_id = next((value for value in old_ids if value not in assigned_title_ids), None)
        if title_id is not None:
            assigned_title_ids.add(title_id)
        display_name = unique_display_name(candidate.name, candidate.root, MEDIA_ROOT, used_names[kind])
        plan.append({"candidate": candidate, "kind": kind, "title_id": title_id,
                     "name": display_name, "groups": season_groups(candidate) if kind == "series" else []})

    backup_stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = DATA_ROOT / f"loyt.sqlite3.before-hierarchy-{backup_stamp}"
    with connect() as source:
        import sqlite3
        with sqlite3.connect(backup) as target:
            source.backup(target)

    timestamp = now()
    manifests: list[tuple[str, str]] = []
    direct_count = hls_count = reused_count = title_count = 0
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TEMP TABLE affected_titles(id INTEGER PRIMARY KEY)")
        conn.executemany("INSERT INTO affected_titles(id) VALUES(?)",
                         ((title_id,) for title_id in affected_title_ids))

        managed_episode_ids = [row["id"] for row in imported_existing.values()]
        if managed_episode_ids:
            conn.executemany("UPDATE episodes SET season_id=NULL WHERE id=?",
                             ((episode_id,) for episode_id in managed_episode_ids))
        if stale:
            conn.executemany("DELETE FROM episodes WHERE source_path=?",
                             ((source_path,) for source_path in stale))
        conn.execute("""UPDATE titles SET poster_episode_id=NULL
                      WHERE poster_episode_id IS NOT NULL
                        AND poster_episode_id NOT IN (SELECT id FROM episodes)""")
        conn.execute("""DELETE FROM seasons
                      WHERE title_id IN (SELECT id FROM affected_titles)
                        AND id NOT IN (SELECT season_id FROM episodes WHERE season_id IS NOT NULL)""")

        # Temporary names avoid collisions while old titles are merged or split.
        for title_id in affected_title_ids:
            conn.execute("UPDATE titles SET name=? WHERE id=?",
                         (f"__loyt_sync_{backup_stamp}_{title_id}", title_id))

        for item in plan:
            if item["title_id"] is None:
                cur = conn.execute(
                    "INSERT INTO titles(kind,name,synopsis,year,created_at) VALUES(?,?,?,NULL,?)",
                    (item["kind"], item["name"], "", timestamp),
                )
                item["title_id"] = int(cur.lastrowid)
                title_count += 1
            else:
                conn.execute("UPDATE titles SET kind=?,name=? WHERE id=?",
                             (item["kind"], item["name"], item["title_id"]))

            groups = item["groups"]
            if item["kind"] == "movie":
                path = item["candidate"].files[0]
                episode_name = prepare_episode(path, 1)["name"] or item["name"]
                source_path = path.relative_to(MEDIA_ROOT).as_posix()
                existing_row = existing.get(source_path)
                if existing_row:
                    conn.execute("""UPDATE episodes SET title_id=?,season_id=NULL,number=1,name=?,source_path=?
                                  WHERE id=?""",
                                 (item["title_id"], episode_name, source_path, existing_row["id"]))
                    reused_count += 1
                else:
                    status = "ready" if path.suffix.lower() in DIRECT else "queued"
                    cur = conn.execute(
                        """INSERT INTO episodes(title_id,season_id,number,name,source_path,status,poster_state,created_at)
                           VALUES(?,NULL,1,?,?,?,'pending',?)""",
                        (item["title_id"], episode_name, source_path, status, timestamp),
                    )
                    if status == "queued":
                        conn.execute("INSERT INTO media_jobs(episode_id,state,created_at,updated_at) VALUES(?,'queued',?,?)",
                                     (cur.lastrowid, timestamp, timestamp))
                        hls_count += 1
                    else:
                        direct_count += 1
                continue

            for sequence, group in enumerate(groups, start=1):
                relative_manifest = manifest_path(item["name"], item["title_id"], sequence)
                season_cursor = conn.execute(
                    """INSERT INTO seasons(title_id,number,manifest_path,section_number,section_name,
                       display_number,display_name) VALUES(?,?,?,?,?,?,?)""",
                    (item["title_id"], sequence, relative_manifest, group.section_number,
                     group.section_name, group.season_number, group.season_name),
                )
                season_id = int(season_cursor.lastrowid)
                manifest_episodes: list[dict] = []
                for number, path in enumerate(group.files, start=1):
                    source_path = path.relative_to(MEDIA_ROOT).as_posix()
                    episode_name = prepare_episode(path, number, group.season_name)["name"]
                    episode = existing.get(source_path)
                    if episode:
                        conn.execute("""UPDATE episodes SET title_id=?,season_id=?,number=?,name=?,source_path=?
                                      WHERE id=?""",
                                     (item["title_id"], season_id, number, episode_name, source_path, episode["id"]))
                        reused_count += 1
                    else:
                        status = "ready" if path.suffix.lower() in DIRECT else "queued"
                        cur = conn.execute(
                            """INSERT INTO episodes(title_id,season_id,number,name,source_path,status,poster_state,created_at)
                               VALUES(?,?,?,?,?,?, 'pending',?)""",
                            (item["title_id"], season_id, number, episode_name, source_path, status, timestamp),
                        )
                        if status == "queued":
                            conn.execute("INSERT INTO media_jobs(episode_id,state,created_at,updated_at) VALUES(?,'queued',?,?)",
                                         (cur.lastrowid, timestamp, timestamp))
                            hls_count += 1
                        else:
                            direct_count += 1
                    manifest_episodes.append({"number": number, "name": episode_name, "source_path": source_path})
                manifests.append((relative_manifest, manifest_text(
                    item["name"], group.season_number, manifest_episodes, group.section_number,
                    group.section_name, group.season_name)))

        for title_id in affected_title_ids:
            conn.execute("DELETE FROM titles WHERE id=? AND NOT EXISTS(SELECT 1 FROM episodes WHERE title_id=?)",
                         (title_id, title_id))
        for item in plan:
            refresh_title_poster(conn, int(item["title_id"]))
        for relative, content in manifests:
            target = DATA_ROOT / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    return len(plan), reused_count, direct_count, hls_count, len(stale)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bulk import mounted Loyt media roots")
    parser.add_argument("--apply", action="store_true", help="write titles, episodes, manifests, and HLS jobs")
    parser.add_argument("--sync", action="store_true", help="rebuild folder structure while preserving existing playback assets")
    args = parser.parse_args()

    roots = import_roots()
    files, direct_files, children, counts = scan(roots)
    candidates = build_candidates(files, direct_files, children, counts, roots)
    movies = sum(_kind(item) == "movie" for item in candidates)
    series = len(candidates) - movies
    direct_count = sum(path.suffix.lower() in DIRECT for item in candidates for path in item.files)
    hls_files = [path for item in candidates for path in item.files if path.suffix.lower() not in DIRECT]
    hls_bytes = sum(path.stat().st_size for path in hls_files)
    hls_free = shutil.disk_usage(DATA_ROOT / "hls").free
    print(f"videos={len(files)} movies={movies} series={series} episodes={sum(len(x.files) for x in candidates)}")
    print(f"direct_mp4={direct_count} hls_sources={len(hls_files)} hls_source_size_gib={hls_bytes / 1024**3:.1f}")
    print(f"free_hls_space_gib={hls_free / 1024**3:.1f}")
    print("title_preview:")
    for item in candidates[:30]:
        kind = _kind(item)
        season_count = len(season_groups(item)) if kind == "series" else 0
        print(f"  {kind}: {item.name} — {len(item.files)} episode(s), {season_count} season(s)")
    if args.sync:
        result = sync_import(candidates, roots)
        print(f"synced_titles={result[0]} reused_episodes={result[1]} new_direct={result[2]} new_hls_jobs={result[3]} stale_removed={result[4]}")
        return 0
    if not args.apply:
        print("dry_run=1 (use --apply to import)")
        return 0

    minimum_free = int(hls_bytes * 1.2 + 1024**3)
    if hls_free < minimum_free:
        print("ERROR: HLS cache does not have enough free space for the non-MP4 sources.", file=sys.stderr)
        return 2
    result = apply_import(candidates)
    print(f"imported_titles={result[0]} imported_episodes={result[1]} direct_mp4={result[2]} hls_jobs={result[3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
