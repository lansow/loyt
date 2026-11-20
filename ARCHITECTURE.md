# Loyt Architecture

This document describes the current implementation. Loyt is a single application for a video library on a trusted network; it does not require a separate database service or queue system.

## Overview

```text
Browser
  └── Nginx
        ├── HTML, CSS, JavaScript, and local fonts
        └── FastAPI
              ├── SQLite in /data
              ├── Original files under /media (read-only)
              ├── Media worker: HLS queue, FFmpeg, and ffprobe
              └── Poster worker: MP4 frames
```

Compose defines four services:

| Service | Responsibility |
| --- | --- |
| `web` | Nginx delivery of the interface, API proxy, and video streams |
| `api` | FastAPI, administrator sessions, catalog, database, and path validation |
| `worker` | Claims queued jobs and builds HLS output and posters for non-MP4 inputs |
| `poster-worker` | Generates posters for MP4 files that play directly |

The interface is Persian and right-to-left, built with HTML, CSS, and JavaScript. The web image downloads pinned HLS.js and font dependencies at build time; the running interface does not require a CDN or external service. The API uses FastAPI, Uvicorn, PyYAML, and Python's standard SQLite module. FFmpeg and ffprobe are included in the API image.

## Data and persistence

SQLite uses write-ahead logging (WAL), foreign keys, and a busy timeout. The main tables are:

- `titles`: movies or series, description, year, poster, and visibility or trash state.
- `seasons`: number, display name, order, and path to the generated YAML manifest.
- `episodes`: number, name, source path relative to `/media`, duration, processing state, and poster.
- `media_jobs`: job state, error information, and queue and processing timestamps.

Persistent files live under `/data`: `loyt.sqlite3`, posters, generated YAML manifests, and timeline preview frames. HLS segments live under `/data/hls`, which can use a separate mount. Back up both data locations. The media mount contains the original files and is managed separately.

Media states are stored in the database. A worker claims a queued job at runtime, periodically refreshes its status, and re-queues abandoned jobs. Errors are kept for the administrator, who can retry a failed job.

## Content intake and preparation

### Movie or single-video title

The administrator submits a title, optional description and year, and a source path. The API resolves the path and confirms that it remains under `/media`, that the file exists, and that its extension is supported. This rejects traversal and symlinks that resolve outside the mount.

The source inspection uses ffprobe to check for a video stream, codec, dimensions, and duration. FFmpeg extracts a sample image. The original file is not modified.

### Series from YAML

The YAML contains a series name, season number, and a list of episode numbers, optional names, and source paths. The API validates the complete structure, checks paths relative to `/media`, and creates the season and episode records in the database. A corresponding YAML file is generated in the application data directory; the media library does not need to be writable.

### Import from a folder

The administrator interface lists folders under `/media`. The preview includes eligible video files and parses names matching `SxxEyy`; other filenames are sorted naturally. After confirmation, the API records the same source paths and generates the season YAML. Files are not copied or moved.

### Optional command-line tools

`python -m app.import_library` performs a bulk import. By default it scans under `/media`; `IMPORT_ROOTS` can provide a comma-separated list of existing subdirectories under `/media`. The default mode is a dry run. `--apply` writes the imported content. `--sync` reconciles the database with scanned files and removes database records for sources no longer in the selected scans. Review the dry-run output and back up the database before using `--sync`.

`python -m app.normalize_library` is an optional tool for cleaning up display names. It first prints the planned changes; `--apply` writes the database and YAML files. Both tools require the mounts and data directory for the same installation and should not be run against a live instance without a backup.

## Processing and playback

- A file with an `.mp4` extension is recorded as ready and served directly from its source with HTTP Range support. `poster-worker` checks its duration and video stream and generates a poster frame separately.
- Other supported video extensions enter the job queue. The `worker` inspects streams with ffprobe, creates a poster, and packages HLS with fMP4 segments.
- H.264 video and AAC audio streams are copied when compatible. If the video codec is not compatible with H.264, FFmpeg transcodes it to H.264; incompatible audio is transcoded to AAC. Some files therefore require more CPU and HLS cache space.
- The poster frame is taken near 12% of the video's duration. If extraction fails, the worker tries the beginning of the file. A series poster is selected from a ready episode.
- The API creates a signed, time-limited playback token. Nginx serves MP4 with Range support and buffering disabled; HLS segments are read through an internal Nginx route.
- The player uses native HLS in compatible browsers and HLS.js in browsers with Media Source Extensions. Final compatibility depends on the codec and browser.
- Timeline preview frames are generated on demand and cached in the data directory for reuse.

## API surface

Main public routes:

| Route | Purpose |
| --- | --- |
| `GET /api/health` | API health check |
| `GET /api/catalog` and `GET /api/search` | Catalog and search |
| `GET /api/title/{id}` and `GET /api/episode/{id}` | Title details, episode details, and next episode |
| `GET /api/play/{id}` | Create a time-limited playback URL |
| `GET /api/source/{id}` and `GET /api/stream/{id}/...` | Deliver MP4 or HLS media |

The `/api/admin/...` routes support administrator login and logout, source browsing and inspection, adding movies and series, folder preview and import, editing content, managing visibility, and viewing or retrying jobs. These routes require the administrator session cookie.

## Security boundaries

- `/media` is mounted read-only in all services that read source files. `/data` is writable by the API and workers; `web` only reads generated images and HLS output.
- The API accepts only paths that resolve beneath the media root. Source paths are stored relative to `/media` in the database.
- The administrator password and signing key are read from environment variables. Administrator sessions are HMAC-signed and valid for 12 hours. The cookie is `HttpOnly` and `SameSite=Lax`; `COOKIE_SECURE=1` enables its Secure attribute.
- Playback tokens are tied to an episode ID and expiry time, and are checked when serving a source file or segment.
- The public catalog does not require login. Loyt has no viewer accounts, per-viewer access levels, or cross-device watch progress; progress stays in the browser's local storage.
- TLS is not configured in Compose. Access outside a trusted network should be placed behind HTTPS and appropriate access controls.

## Compose settings

| Variable | Purpose |
| --- | --- |
| `MEDIA_HOST_PATH` | Host media library mounted inside containers at `/media` |
| `APP_DATA_PATH` | Persistent directory for the database and generated files |
| `HLS_DATA_PATH` | HLS cache directory |
| `WEB_PORT` | Published web interface port |
| `PUID`, `PGID` | IDs that need media read access and data write access |
| `ADMIN_PASSWORD` | Administrator password; at least 12 characters |
| `SESSION_SECRET` | Session signing key; at least 32 characters |
| `COOKIE_SECURE` | Enables the Secure cookie attribute behind HTTPS |

`compose.yaml` mounts paths with `create_host_path: false`; host directories must exist beforehand. `compose.multiroot.example.yaml` shows an example with multiple media mounts. The local `compose.override.yaml` file is ignored by Git.

## Current limitations

- Administrator access uses one shared password; there is no separate user or role management.
- Watch progress is local to each browser and is not synchronized across devices.
- Loyt does not upload or delete video files and does not create backups of original media.
- Non-MP4 formats may need transcoding and extra storage; processing time depends on file size, codecs, and CPU capacity.
- The current interface does not manage subtitles or selection of multiple video and audio tracks.
