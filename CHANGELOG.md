# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.2] - 2026-09-21
### Added
- The OCR service's LLM result (model, pages, corrections, failed/transcribed pages) is written to the log for every file

## [0.5.1] - 2026-09-21
### Added
- `OCR_TYPE` (`exact` default, or `deskew`): chooses how the OCR service treats the scanned image; `deskew` straightens pages that were fed in crooked. The value is sent to the service as field `type` and shown in the start-up log line

## [0.5.0] - 2026-09-20
### Added
- `DEPLOYMENT.md`: step-by-step guide (German) for running the proxy with the OCR chain on a VM
- Optional OCR pipeline (`OCR_URL`): PDFs are processed by the pdf-adobe-ocr service and filed as
  `<correspondent>/<date>_<content>.pdf`; fallbacks `_Unbekannt`, `_Pruefen` and `<timestamp>_Fehler.pdf`
- Files in the target are never overwritten in this mode (collisions get `_2`, `_3`, ...); results are written atomically
- `POLL_INTERVAL` and `SETTLE_SECONDS` settings (defaults unchanged: 10 s / 30 s)
- More patient defaults for an unreachable OCR service: 5 attempts 60 s apart (was 3 x 30 s)
- Long documents (over the OCR service's page limit for LLM work) are still OCR'd: the service skips only the LLM correction in `lenient` mode instead of rejecting the file, so they no longer end up as `_Fehler`
- Pages where Adobe found only noise (very faded receipts) get a fresh transcription from the LLM; only lines the model is sure about are written, poorly legible pages go to `_Pruefen` with the scan date
- A finished OCR result is kept until it has been written to the target: a target outage no longer causes repeated (billed) OCR runs
- Integration test with stub OCR service (`test/run.sh`), unit tests (`test/test_watcher.py`), a compose file for tests against a real OCR service and an example compose file

## [0.4.0] - 2021-03-12
### Release
- Clone and adjustment to allow all files to be copied

## [0.3.2] - 2020-10-27
### Fixed
- Fixed additional error when container is restarted

## [0.3.1] - 2020-10-27
### Fixed
- Fixed error when container is restarted instead of recreated

## [0.3.0] - 2020-05-18
### Added
- Multiple Shares can be defined

### Changed
- Config names are changed to allow multiple shares.

## [0.2.0] - 2020-04-28
### Added
- Changelog
### Changed
- Specialized and renamed image for scanning purposes
- The samba server doesnt work directly on the mounted folder. Instead a script will move the files to the mounted folder.

## [0.1.1] - 2020-04-21
### Added
- Docker healthcheck for mounted folder

## [0.1.0] - 2020-04-20
### Added
- Initial Files
