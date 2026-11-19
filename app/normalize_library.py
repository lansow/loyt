from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from app.import_library import manifest_text
from app.main import DATA_ROOT, MEDIA_ROOT, connect
from app.naming import (
    episode_file_title, is_generic_folder, movie_file_title, series_folder_title,
    unique_display_name,
)


def episode_rows(conn, title_id: int) -> list[dict]:
    return [dict(row) for row in conn.execute(
        """SELECT e.id,e.number,e.name,e.source_path,e.season_id,s.number AS season_number
           FROM episodes e LEFT JOIN seasons s ON s.id=e.season_id
           WHERE e.title_id=? ORDER BY COALESCE(s.number,0),e.number""", (title_id,)
    )]


def common_folder(paths: list[Path]) -> Path:
    if not paths:
        return MEDIA_ROOT
    return Path(os.path.commonpath([str(path.parent) for path in paths]))


def make_plan() -> tuple[list[dict], list[dict], list[dict]]:
    with connect() as conn:
        titles = conn.execute("SELECT id,kind,name FROM titles ORDER BY id").fetchall()
        used: dict[str, set[str]] = {"movie": set(), "series": set()}
        title_plan: list[dict] = []
        episode_plan: list[dict] = []
        manifest_plan: list[dict] = []

        for title in titles:
            episodes = episode_rows(conn, title["id"])
            if not episodes:
                continue
            sources = [MEDIA_ROOT / episode["source_path"] for episode in episodes]
            folder = common_folder(sources)
            if title["kind"] == "series":
                old_name = title["name"].strip()
                address_like = re.search(r"/|\\", old_name)
                if old_name and not address_like and not is_generic_folder(old_name):
                    base = old_name
                else:
                    base = series_folder_title(folder, MEDIA_ROOT)
            else:
                base = movie_file_title(sources[0], MEDIA_ROOT)
            new_name = unique_display_name(base, folder, MEDIA_ROOT, used[title["kind"]])
            title_plan.append({"id": title["id"], "kind": title["kind"],
                               "old": title["name"], "new": new_name, "folder": folder})

            for episode, source in zip(episodes, sources):
                new_episode = episode_file_title(source, episode["number"], MEDIA_ROOT)
                episode_plan.append({"id": episode["id"], "old": episode["name"], "new": new_episode})

            season_rows = conn.execute(
                """SELECT id,number,manifest_path,section_number,section_name,display_number,display_name
                   FROM seasons WHERE title_id=? ORDER BY section_number,display_number,number""",
                (title["id"],),
            ).fetchall()
            planned_episode_names = {item["id"]: item["new"] for item in episode_plan}
            for season in season_rows:
                items = conn.execute(
                    "SELECT id,number,source_path FROM episodes WHERE season_id=? ORDER BY number",
                    (season["id"],),
                ).fetchall()
                manifest_episodes = [
                    {"number": row["number"], "name": planned_episode_names[row["id"]],
                     "source_path": row["source_path"]}
                    for row in items
                ]
                manifest_plan.append({
                    "relative": season["manifest_path"],
                    "content": manifest_text(new_name, season["display_number"], manifest_episodes,
                                              season["section_number"], season["section_name"],
                                              season["display_name"]),
                })
        return title_plan, episode_plan, manifest_plan


def safe_manifest(relative: str) -> Path:
    path = (DATA_ROOT / relative).resolve()
    if not path.is_relative_to(DATA_ROOT) or path.suffix.lower() not in {".yml", ".yaml"}:
        raise RuntimeError(f"مسیر فایل فهرست معتبر نیست: {relative}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean visible titles from their source folders and filenames")
    parser.add_argument("--apply", action="store_true", help="update database names and season YAML manifests")
    args = parser.parse_args()

    title_plan, episode_plan, manifest_plan = make_plan()
    changed_titles = [item for item in title_plan if item["old"] != item["new"]]
    changed_episodes = [item for item in episode_plan if item["old"] != item["new"]]
    path_like_names = [item for item in title_plan if re.search(r"/|\\", item["new"])]
    print(f"titles={len(title_plan)} changed_titles={len(changed_titles)} changed_episode_names={len(changed_episodes)}")
    print(f"manifests_to_refresh={len(manifest_plan)} path_like_titles={len(path_like_names)}")
    print("title_preview:")
    sample = [item for item in changed_titles if item["old"] != item["new"]]
    for item in sample[:45]:
        print(f"  [{item['kind']}] {item['old']}  ->  {item['new']}")
    if path_like_names:
        print("WARNING: some title names still look like paths:")
        for item in path_like_names[:10]:
            print(f"  {item['id']}: {item['new']}")
        return 2

    if not args.apply:
        print("dry_run=1; use --apply to save the new display names")
        return 0

    backup = DATA_ROOT / "loyt.sqlite3.before-title-cleanup"
    if not backup.exists():
        with connect() as source:
            import sqlite3
            with sqlite3.connect(backup) as target:
                source.backup(target)

    for item in manifest_plan:
        safe_manifest(item["relative"]).parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for item in title_plan:
            conn.execute("UPDATE titles SET name=? WHERE id=?", (item["new"], item["id"]))
        for item in episode_plan:
            conn.execute("UPDATE episodes SET name=? WHERE id=?", (item["new"], item["id"]))
        for item in manifest_plan:
            safe_manifest(item["relative"]).write_text(item["content"], encoding="utf-8")

    print(f"saved_titles={len(changed_titles)} saved_episode_names={len(changed_episodes)} backup={backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
