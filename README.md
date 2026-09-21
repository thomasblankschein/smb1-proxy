Slightly changed fork of https://github.com/emtek-at/smb1-proxy
* Faster check interval (10s)
* File name with underscore (no blank)
* File name based on last modified date of the file

# smb1-proxy #

This container is used to proxy an existing secure smb share (version 2+) to allow legacy devices, that only support cifs/smb v1 the access to a specific share or folder on the secure share - without downgrading the complete server to smb v1. Its designed to forward all files to the secure share, without overwriting files on the destination

## Usage ##

Example docker-compose configuration:

```yml
version: '3.7'

services:
  smb1proxy:
    image: smb1-proxy
    environment:
      TZ: 'Europe/Berlin'
      USERID: 1000
      GROUPID: 1000
      SAMBA_USERNAME: scanuser
      SAMBA_PASSWORD: secret1
      GLOBAL: ntlm auth = yes
      PROXY1_ENABLE: 1
      PROXY1_SHARE_NAME: scanshare10
      PROXY1_REMOTE_PATH: //secure-host/share/path/to/folder
      PROXY1_REMOTE_DOMAIN: DOM
      PROXY1_REMOTE_USERNAME: UserA
      PROXY1_REMOTE_PASSWORD: password
      PROXY2_ENABLE: 1
      PROXY2_SHARE_NAME: scanshare30
      PROXY2_REMOTE_PATH: //other-host/share
      PROXY2_REMOTE_DOMAIN: DOM
      PROXY2_REMOTE_USERNAME: UserB
      PROXY2_REMOTE_PASSWORD: password
    ports:
      - "445:445/tcp"
    tmpfs:
      - /tmp
    restart: unless-stopped
    stdin_open: true
    tty: true
    privileged: true
```

## Config ##

The configuration is done via environment variables:

- `TZ`: Timezone
- `USERID`: Linux User ID
- `GROUPID`: Linux Group ID
- `SAMBA_USERNAME`: Global Username for the created shares
- `SAMBA_PASSWORD`: Global Password for the created shares
- `GLOBAL`: ntlm auth = yes ; Windows XP legacy support
- `PROXYx_ENABLE`: 0 = disabled, 1 = enabled
- `PROXYx_SHARE_NAME`: Samba Share name
- `PROXYx_REMOTE_PATH`: Can be just a share (//host/share) or a complete path (//host/share/path/to/folder)
- `PROXYx_REMOTE_DOMAIN`: Domain for remote path (optional)
- `PROXYx_REMOTE_USERNAME`: Username for remote path
- `PROXYx_REMOTE_PASSWORD`: Password for remote path

You can substitute x with an incremented number starting at 1 to create multiple entries. See example configuration.

## OCR pipeline (optional) ##

With `OCR_URL` set, PDFs are no longer just renamed: the watcher sends each finished PDF to the
[pdf-adobe-ocr](https://github.com/thomasblankschein/pdf-adobe-ocr) service (`POST /api/ocr` with `llm=true`, `meta=true`,
`lenient=true`), and stores the result in the target share. Without `OCR_URL` nothing changes.
A complete example with both services is in `docker-compose.ocr.example.yml`; a step-by-step guide for the target VM (in German) is in [DEPLOYMENT.md](DEPLOYMENT.md).

Where a file ends up in the target (one folder level, files are never overwritten - collisions get `_2`, `_3`, ...):

| Situation | Target |
|---|---|
| OCR and metadata ok | `<Correspondent>/<Date>_<Content>.pdf` |
| Sender not recognised | `_Unbekannt/<Date>_<Content>.pdf` |
| Model unsure about date/sender | `_Pruefen/<Date>_<Correspondent>_<Content>.pdf` |
| OCR ok, metadata missing or unusable | `_Unbekannt/<timestamp>.pdf` (with text layer) |
| OCR failed for good (service down, error, timeout) | `<timestamp>_Fehler.pdf` in the target root (original, no OCR) |
| Not a PDF (or no PDF header) | `<timestamp>.<ext>`, as before |
| Target not writable | file stays in the share, next attempt after `COOLDOWN_SECONDS`; the finished OCR result is kept in memory, so the file is **not** sent to OCR (and billed) again |

A scan is never lost: the original is only deleted after the result has been written (as `.part` file first, then renamed).
Temporary errors (connection, timeout, HTTP 5xx/429) are retried; other HTTP 4xx (e.g. wrong API key, file too large) are not.
The path proposed by the service is validated (exactly `<folder>/<file>.pdf`, no `..`, no separators) before it is used.

Additional environment variables (all optional):

- `OCR_URL`: Base URL of the OCR service, e.g. `http://pdf-adobe-ocr:3000`. Empty = OCR pipeline off
- `OCR_API_KEY`: API key of the service (`X-API-Key`)
- `OCR_LANG`: OCR language, default `de-DE`
- `OCR_TYPE`: `exact` (default) keeps the scanned image unchanged and only adds the text layer; `deskew` also straightens pages that were fed in crooked (the image is modified). An invalid value falls back to `exact` with a message in the log
- `OCR_TIMEOUT`: Seconds per request, default `900` (runs with an LLM take minutes)
- `OCR_RETRIES`: Attempts per file, default `5`; `RETRY_DELAY`: seconds between attempts, default `60` (so an OCR service that is restarted or updated within about 4 minutes is bridged)
- `OCR_WORKERS`: Files processed in parallel, default `2`
- `DELIVERY_RETRIES`: Attempts to write to the target per round, default `3` (a hanging SMB mount blocks each attempt for about a minute); `COOLDOWN_SECONDS`: pause afterwards, default `300`
- `POLL_INTERVAL` (default `10`) and `SETTLE_SECONDS` (default `30`): check interval and how long a file must be unchanged

Things to know:

- Every PDF that arrives is sent to Adobe and - if configured - to the LLM provider of the OCR service.
- Files in the share live in the container's file system. They survive `docker restart`, but **not** re-creating the container (`docker compose up` after an image update): mount a volume on `/share1` (and `/share2`, ...) if a scan could be in flight while you update.
- If the OCR service is unreachable, a file is retried `OCR_RETRIES` times `RETRY_DELAY` seconds apart (default 5 x 60 s, about 4 minutes). If the outage lasts longer, the original is filed as `<timestamp>_Fehler.pdf` and is **not** retried automatically later - raise `OCR_RETRIES`/`RETRY_DELAY` if your outages (updates, backups) take longer. Only the workers that hold such a file are busy while it waits; with `OCR_WORKERS` files in retry, further scans queue up in the share until a worker is free.
- A wrong `OCR_API_KEY` is not retried: every scan is filed as `_Fehler.pdf` until it is fixed (nothing is lost, but there is no notification).
- Restarting the proxy while a file is being processed processes that file again (one more OCR run).

### Tests ###

- `python3 test/test_watcher.py`: unit tests for the OCR chain (retries, fallbacks, no repeated OCR after a delivery error, collisions, path validation) - no Docker, no network, no costs.

- `bash test/run.sh`: starts the proxy, an SMB2 test target, a stub OCR service and an SMB1 client with Docker Compose
  and checks the folder layout, collisions, fallbacks and retries (no Adobe, no LLM, no costs).
- `test/docker-compose.real.yml`: the same stack against a **running, real** pdf-adobe-ocr service (costs money): `OCR_API_KEY=... docker compose -p realtest -f test/docker-compose.real.yml up -d --build`, then upload with `smbclient -m NT1`.
