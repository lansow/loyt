from __future__ import annotations

import re
from pathlib import Path


GENERIC_DIRS = {
    "zip", "movie", "movies", "film", "films", "series", "tv",
    "video", "videos", "files", "media", "content",
    "فیلم‌ها", "سریال‌ها", "ویدیو", "ویدیوها", "محتوا",
}


def clean_title(value: str) -> str:
    value = re.sub(r"\s+@[A-Za-z0-9_]{2,}\b", "", value)
    value = re.sub(r"\s+", " ", value).strip(" ._-—")
    return value[:200] or "عنوان بدون نام"


def clean_episode(path: Path) -> str:
    value = path.stem
    value = re.sub(r"(?i)(?:-[a-z0-9]{4})?-(?:[a-z0-9-]+\.)+(?:com|net|org|ir)(?:[-_.].*)?$", "", value)
    value = re.sub(r"(?i)(?:-[a-z0-9]{4})?-(?:[a-z0-9]+-){1,4}(?:com|net|org|ir)(?:[-_.].*)?$", "", value)
    value = re.sub(r"\s+@[A-Za-z0-9_]{2,}\b", "", value)
    value = re.sub(r"(?i)S\d{1,3}E\d{1,4}", "", value)
    value = re.sub(r"^\d{1,4}[ ._-]+", "", value)
    value = re.sub(r"[._]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" -_—")
    return value[:180]


def folder_label(value: str) -> str:
    value = re.sub(r"\s+@[A-Za-z0-9_]{2,}\b", "", value)
    value = re.sub(r"(?i)^(?:season|s)[ _.-]?\d{1,3}[ _.-]*", "", value)
    value = re.sub(r"^\d{1,4}[ ._-]+", "", value)
    value = re.sub(r"\s+", " ", value).strip(" ._-—")
    return value[:160]


def _folder_key(value: str) -> str:
    return re.sub(r"[\W_]+", " ", folder_label(value).casefold(), flags=re.UNICODE).strip()


def is_generic_folder(value: str) -> bool:
    key = _folder_key(value)
    generic = {_folder_key(item) for item in GENERIC_DIRS}
    return (not key or key in generic or key.isdecimal()
            or re.fullmatch(r"مجموعه \d+", key) is not None)


def _nearby_context(directory: Path, media_root: Path, include_self: bool) -> list[str]:
    cursor = directory if include_self else directory.parent
    output: list[str] = []
    while cursor != media_root and cursor.is_relative_to(media_root):
        parts = cursor.relative_to(media_root).parts
        if len(parts) > 1:
            label = folder_label(cursor.name)
            if label.isdecimal():
                label = f"بخش {label}"
            elif is_generic_folder(label):
                label = ""
            if label and _folder_key(label) not in {_folder_key(item) for item in output}:
                output.append(label)
        cursor = cursor.parent
    return output


def series_folder_title(folder: Path, media_root: Path) -> str:
    label = folder_label(folder.name)
    if not is_generic_folder(label):
        return clean_title(label)
    for ancestor in folder.parents:
        if ancestor == media_root or not ancestor.is_relative_to(media_root):
            break
        if ancestor.parent == media_root:
            break
        candidate = folder_label(ancestor.name)
        if not is_generic_folder(candidate):
            return clean_title(candidate)
    return "مجموعه ویدیویی"


def movie_file_title(path: Path, media_root: Path) -> str:
    label = clean_episode(path) or folder_label(path.parent.name) or "ویدیو"
    if label.isdecimal():
        context = _nearby_context(path.parent, media_root, include_self=True)
        if context:
            label = f"{label} — {context[0]}"
    return clean_title(label)


def episode_file_title(path: Path, number: int, media_root: Path,
                       preferred_context: str = "") -> str:
    label = clean_episode(path)
    if label.isdecimal():
        context = folder_label(preferred_context)
        if is_generic_folder(context):
            nearby = _nearby_context(path.parent, media_root, include_self=True)
            context = nearby[0] if nearby else ""
        return clean_title(f"{label} — {context}" if context else f"قسمت {number}")
    return clean_title(label or f"قسمت {number}")


def unique_display_name(base: str, folder: Path, media_root: Path,
                        used: set[str]) -> str:
    base = clean_title(base)
    if base.casefold() not in used:
        used.add(base.casefold())
        return base
    for context in _nearby_context(folder, media_root, include_self=False):
        if _folder_key(context) == _folder_key(base):
            continue
        candidate = clean_title(f"{base} — {context}")
        if candidate.casefold() not in used:
            used.add(candidate.casefold())
            return candidate
    version = 2
    while f"{base} (نسخه {version})".casefold() in used:
        version += 1
    candidate = f"{base} (نسخه {version})"
    used.add(candidate.casefold())
    return candidate
