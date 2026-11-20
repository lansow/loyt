# Loyt

English | [فارسی](README.fa.md)

Loyt is a Persian, right-to-left video library and player for movies and series. People on the network can browse, search, and play published titles. An administrator adds and organizes content from media files already available to the server. Loyt reads source media without uploading, moving, or deleting it.

## Features

- Movie and series catalog, title and episode search, title details, and season views.
- Add movies or single-video titles, create a season from YAML, or preview a folder import before saving it.
- Edit title, description, year, season and episode names and numbers; hide, restore, or move titles to the trash.
- Background media job queue with status and retry after errors.
- Posters and timeline previews generated from video frames.
- Direct MP4 playback with seeking, and HLS playback for other supported formats.
- Continue-watching progress saved in the current browser and device.

## Architecture

```text
Browser
  └── Nginx: web interface, API proxy, and protected media delivery
        └── FastAPI: catalog, admin tools, validation, and playback
              ├── SQLite: catalog and media job queue
              ├── Media worker: HLS preparation and posters for non-MP4 inputs
              └── Poster worker: posters for MP4 files

Original media: /media (read-only)
Database and generated files: /data
```

Docker Compose runs four services: `web`, `api`, `worker`, and `poster-worker`. The web interface uses HTML, CSS, and JavaScript. The API uses FastAPI and SQLite; FFmpeg and ffprobe are included in the Python service image.

## Requirements

- Docker Engine and the Docker Compose plugin.
- A media directory readable by the configured container user.
- Persistent writable directories for application data and, optionally, the HLS cache.

FFmpeg does not need to be installed on the host.

## Quick start

1. Copy the sample settings and create the local directories:

   ```sh
   cp .env.example .env
   mkdir -p ./media ./data ./data/hls
   ```

2. Edit `.env`. Set `MEDIA_HOST_PATH` to your media directory. Choose an administrator password of at least 12 characters and a random `SESSION_SECRET` of at least 32 characters. For example, generate a secret with `openssl rand -hex 32`. Keep these values in the local `.env` file.

   | Variable | Purpose | Example |
   | --- | --- | --- |
   | `MEDIA_HOST_PATH` | Host media library, mounted read-only | `./media` |
   | `APP_DATA_PATH` | Database, posters, previews, and generated YAML | `./data` |
   | `HLS_DATA_PATH` | HLS cache; can be moved to a separate disk | `./data/hls` |
   | `WEB_PORT` | Published web port | `8080` |
   | `PUID`, `PGID` | User and group IDs for media reads and data writes | `1000` |
   | `ADMIN_PASSWORD` | Shared administrator password; replace the sample | Set locally |
   | `SESSION_SECRET` | Signing key for sessions and playback URLs; replace the sample | Set locally |
   | `COOKIE_SECURE` | Enables the Secure cookie attribute when using HTTPS | `0` for direct local-network access |

3. Confirm that the media and data directories exist. Compose deliberately does not create missing host paths, so a typo will not silently mount an empty directory.

4. Build and start the services:

   ```sh
   docker compose up -d --build
   ```

5. Open the server address and configured port in a browser. The API health endpoint is `/api/health`. Open the administrator tools from **مدیریت (Manage)** in the interface.

Check service status and logs with:

```sh
docker compose ps
docker compose logs -f api worker poster-worker
```

## Media mounts

Compose mounts `MEDIA_HOST_PATH` at `/media` inside the containers. In administrator forms, use paths inside the container, such as `/media/Movies/example.mp4`; the host's actual directory path does not need to be stored in a title or YAML manifest. The application resolves each path and rejects files outside the mounted media root, including symlinks that resolve outside it.

To mount multiple host directories, use `compose.multiroot.example.yaml`. Set `MOVIES_HOST_PATH` and `SERIES_HOST_PATH` only in your local `.env` file, then start both Compose files:

```sh
docker compose -f compose.yaml -f compose.multiroot.example.yaml up -d --build
```

Each host directory is mounted under its corresponding location inside `/media`. The base `MEDIA_HOST_PATH` must still exist. Media mounts are read-only; Loyt does not need to write to them.

## Adding content

### Movie or single-video title

In the administrator tools, choose **افزودن محتوا (Add content)** and then the movie option. Enter a title, with an optional description and year, and provide a source path under `/media`. The source check verifies that the file exists and contains a video stream, reads its duration and basic properties, and displays a sample frame.

### Series from YAML

Enter the series title, season, and episodes. A YAML manifest has this general structure:

```yaml
series: "Example Series"
season: 1
episodes:
  - number: 1
    title: "Episode One"
    source: "/media/Series/example/s01e01.mkv"
  - number: 2
    source: "/media/Series/example/s01e02.mkv"
```

Episode titles are optional. The interface previews the completed YAML before saving. The API validates the full list; duplicate numbers, invalid paths, or missing files prevent a partial season from being saved. Generated manifests are kept in the application data directory.

### Import a season from a folder

Choose a directory under `/media` and review the preview. Names such as `S01E02` provide season and episode numbers; other video files are sorted naturally by name. After confirmation, Loyt creates the YAML manifest and records the source paths. It does not copy or move the media files.

After content is added, preparation status appears in **کارهای رسانه (Media jobs)**. If processing fails, the error is shown there and an administrator can retry the job.

## Playback and storage

- Files with an `.mp4` extension play directly from the original source using HTTP Range requests. Their posters are generated by a separate worker.
- Other supported video formats are packaged as HLS with fMP4 segments. H.264 video and AAC audio streams can be copied when compatible; other streams are transcoded to H.264 and AAC for browser playback. Transcoding can use substantial CPU time and disk space.
- Posters are generated from a frame near the start of a video, with a fallback attempt at the beginning if extraction fails. A series poster is selected from a suitable processed episode.
- The player uses native HLS where available and hls.js in browsers that support Media Source Extensions. Playback depends on the device and codecs it supports.
- Continue-watching progress is stored in browser `localStorage`. There are no viewer accounts or cross-device progress sync.

Keep the data directory persistent and writable by the configured user. For backups, stop the services and back up `APP_DATA_PATH` and `HLS_DATA_PATH`. Original media remains separate from Loyt's generated data.

## Privacy and security

- `.env`, `compose.override.yaml`, the database, and local `data` and `media` directories are excluded from Git. The sample environment file contains replaceable example values only.
- The catalog and playback are available to anyone who can reach the service over the network. Only administrative operations require the shared administrator password. Separate viewer accounts are not implemented.
- The administrator password and session key are supplied through environment variables. The administrator cookie is `HttpOnly` and `SameSite=Lax`. Set `COOKIE_SECURE=1` when HTTPS is in use.
- Playback URLs use signed, time-limited tokens. Source paths are restricted to the media root, which is mounted read-only.
- Loyt is intended for a trusted network. Do not expose it to the public internet without HTTPS and an appropriate access-control layer.

## Project layout

```text
app/                              API, content import, and media workers
web/                              Persian interface, player, and styles
compose.yaml                      Main Docker services
compose.multiroot.example.yaml    Example for multiple media directories
Dockerfile                        API and media worker image
web.Dockerfile                    Nginx and web interface image
nginx.conf                        API and media delivery routing
requirements.txt                  Python dependencies
ARCHITECTURE.md                   Data flow, security, and implementation details
```

## Troubleshooting

- **A service does not start:** Check that media and data paths exist and that the configured UID/GID has the required read and write access.
- **Administrator login fails:** Use a password of at least 12 characters and a session key of at least 32 characters; replace the sample values from `.env.example`.
- **A source file cannot be inspected:** Use a path under `/media`, check that the file is in the read-only mount, and confirm that the container user can read it.
- **A job stays queued or fails:** Check the `worker` logs and available HLS disk space. Non-MP4 formats may require additional processing time and storage.
- **Playback does not start:** Check that processing is complete and that the browser supports the media codecs.

See [ARCHITECTURE.md](ARCHITECTURE.md) for details about the database, API, and media processing.
