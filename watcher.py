#!/usr/bin/env python3

"""Verschiebt fertig hochgeladene Dateien aus den Scanner-Freigaben (/shareN) in die SMB2-Ziele (/remoteN).

Ohne OCR_URL (Standard) wie bisher: Datei wird als <Änderungszeit>.<Endung> abgelegt.

Mit OCR_URL werden PDFs zuerst an den pdf-adobe-ocr-Service geschickt (OCR, Textkorrektur, Datum/Korrespondent/
Kurzinhalt). Abgelegt wird das Ergebnis unter <Korrespondent>/<Datum>_<Kurzinhalt>.pdf. Es geht nie ein Scan
verloren:
  - Metadaten fehlen             -> _Unbekannt/<Zeitstempel>.pdf (mit OCR)
  - OCR endgültig fehlgeschlagen -> <Zeitstempel>_Fehler.pdf im Zielordner (Original, ohne OCR)
  - Ziel nicht beschreibbar      -> Datei bleibt in der Freigabe und wird später erneut versucht
"""

import base64, datetime, glob, json, os, shutil, threading, time, urllib.error, urllib.request, uuid
from concurrent.futures import ThreadPoolExecutor

POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', '10'))          # Sekunden zwischen zwei Durchläufen
SETTLE_SECONDS = int(os.getenv('SETTLE_SECONDS', '30'))        # so lange muss eine Datei unverändert sein
OCR_URL = os.getenv('OCR_URL', '').strip().rstrip('/')
OCR_API_KEY = os.getenv('OCR_API_KEY', '')
OCR_LANG = os.getenv('OCR_LANG', 'de-DE')
# exact = Originalbild bleibt unverändert; deskew = schräg eingezogene Scans werden begradigt (Bild wird verändert)
OCR_TYPE = os.getenv('OCR_TYPE', '').strip().lower() or 'exact'
if OCR_TYPE not in ('exact', 'deskew'):
  print("OCR_TYPE '{}' ist ungültig (exact | deskew) - verwende exact".format(OCR_TYPE), flush=True)
  OCR_TYPE = 'exact'
OCR_TIMEOUT = int(os.getenv('OCR_TIMEOUT', '900'))             # Sekunden je Aufruf (LLM-Läufe dauern Minuten)
OCR_RETRIES = max(1, int(os.getenv('OCR_RETRIES', '5')))       # Versuche je Datei (5 x 60 s überbrücken ca. 4 Minuten Ausfall)
OCR_WORKERS = max(1, int(os.getenv('OCR_WORKERS', '2')))       # parallel verarbeitete Dateien
RETRY_DELAY = int(os.getenv('RETRY_DELAY', '60'))              # Sekunden zwischen Versuchen
DELIVERY_RETRIES = max(1, int(os.getenv('DELIVERY_RETRIES', '3')))
COOLDOWN_SECONDS = int(os.getenv('COOLDOWN_SECONDS', '300'))   # Pause, wenn das Ziel nicht beschreibbar war

inflight = set()      # Dateien, die gerade verarbeitet werden
cooldown = {}         # Datei -> frühester nächster Versuch
results = {}          # Datei -> (Änderungszeit, Bytes, Zielpfad): fertiges OCR-Ergebnis, das noch nicht abgelegt werden konnte
state_lock = threading.Lock()
name_lock = threading.Lock()  # Vergabe eindeutiger Dateinamen im Ziel


def log(msg):
  print(datetime.datetime.now(), " - " + msg, flush=True)


# ---------------------------------------------------------------------------
# Ablage im Ziel
# ---------------------------------------------------------------------------

def unique_path(directory, name):
  """Nie überschreiben: bei Kollision _2, _3, ... anhängen."""
  base, ext = os.path.splitext(name)
  candidate = os.path.join(directory, name)
  n = 2
  while os.path.exists(candidate):
    candidate = os.path.join(directory, "{}_{}{}".format(base, n, ext))
    n += 1
  return candidate


def safe_relpath(rel):
  """Vom OCR-Service vorgeschlagener Pfad: genau <Ordner>/<Datei>.pdf, keine Tricks."""
  if not isinstance(rel, str) or '\\' in rel or '\0' in rel:
    return None
  parts = rel.split('/')
  if len(parts) != 2 or not all(p and p not in ('.', '..') and len(p) <= 200 for p in parts):
    return None
  if parts[0].startswith('.') or parts[1].startswith('.') or not parts[1].lower().endswith('.pdf'):
    return None
  return rel


def deliver(remoteMount, rel, data):
  """Schreibt data nach remoteMount/rel: erst als .part, dann umbenennen. Gibt den endgültigen Pfad zurück."""
  directory = os.path.join(remoteMount, os.path.dirname(rel))
  os.makedirs(directory, exist_ok=True)
  part = os.path.join(directory, ".{}.part".format(uuid.uuid4().hex))
  try:
    with open(part, "wb") as f:
      f.write(data)
    with name_lock:
      final = unique_path(directory, os.path.basename(rel))
      os.rename(part, final)
    return final
  except Exception:
    try:
      os.remove(part)
    except OSError:
      pass
    raise


# ---------------------------------------------------------------------------
# OCR-Service
# ---------------------------------------------------------------------------

class OcrError(Exception):
  def __init__(self, message, retryable):
    super().__init__(message)
    self.retryable = retryable


def multipart(fields, filename, data):
  boundary = uuid.uuid4().hex
  body = b""
  for name, value in fields.items():
    body += "--{}\r\nContent-Disposition: form-data; name=\"{}\"\r\n\r\n{}\r\n".format(boundary, name, value).encode()
  body += "--{}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{}\"\r\nContent-Type: application/pdf\r\n\r\n".format(
    boundary, filename.replace('"', '_')).encode()
  body += data + "\r\n--{}--\r\n".format(boundary).encode()
  return body, "multipart/form-data; boundary=" + boundary


def call_ocr(data, filename, scan_date):
  """Schickt die PDF an den Service. Gibt (pdf_bytes, meta_dict_oder_None) zurück, sonst OcrError."""
  fields = {'lang': OCR_LANG, 'type': OCR_TYPE, 'llm': 'true', 'meta': 'true', 'lenient': 'true', 'scan_date': scan_date}
  body, content_type = multipart(fields, filename, data)
  headers = {'Content-Type': content_type}
  if OCR_API_KEY:
    headers['X-API-Key'] = OCR_API_KEY
  request = urllib.request.Request(OCR_URL + '/api/ocr', data=body, headers=headers, method='POST')
  try:
    with urllib.request.urlopen(request, timeout=OCR_TIMEOUT) as response:
      pdf = response.read()
      meta_header = response.headers.get('X-OCR-Meta')
  except urllib.error.HTTPError as err:
    detail = err.read(300).decode('utf-8', 'replace')
    # 4xx (außer Zeitüberschreitung/Ratenlimit) sind endgültig: Wiederholen ändert nichts
    raise OcrError("HTTP {} {}".format(err.code, detail), err.code >= 500 or err.code in (408, 429))
  except (urllib.error.URLError, OSError) as err:
    raise OcrError("nicht erreichbar/Zeitüberschreitung: {}".format(err), True)
  if not pdf.startswith(b'%PDF'):
    raise OcrError("Antwort ist keine PDF", True)
  meta = None
  if meta_header:
    try:
      meta = json.loads(base64.b64decode(meta_header).decode('utf-8'))
    except Exception as err:
      log("Metadaten nicht lesbar ({}) - werden ignoriert".format(err))
  return pdf, meta


# ---------------------------------------------------------------------------
# Verarbeitung
# ---------------------------------------------------------------------------

def is_pdf(file):
  if os.path.splitext(file)[1].lower() != '.pdf':
    return False
  try:
    with open(file, 'rb') as f:
      return f.read(5) == b'%PDF-'
  except OSError:
    return False


def move_legacy(file, remoteMount):
  """Bisheriges Verhalten: <Änderungszeit>.<Endung>."""
  _, filename = os.path.split(file)
  mtime = os.path.getmtime(file)
  name, ext = os.path.splitext(filename)
  mtime_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y%m%d_%H%M%S")
  remotePath = remoteMount + "/" + mtime_str + ext
  try:
    log("Move File: '" + file + "' -> '" + remotePath + "'")
    shutil.copyfile(file, remotePath)
    os.remove(file)
  except (FileNotFoundError, OSError) as err:
    print("↳ " + str(err), flush=True)


def process_pdf(file, remoteMount):
  """OCR-Kette für eine PDF. Die Datei bleibt bei Ablagefehlern in der Freigabe."""
  filename = os.path.basename(file)
  mtime = os.path.getmtime(file)
  mtime_dt = datetime.datetime.fromtimestamp(mtime)
  mtime_str = mtime_dt.strftime("%Y%m%d_%H%M%S")
  scan_date = mtime_dt.strftime("%Y-%m-%d")
  # Ein fertiges Ergebnis, das nur nicht abgelegt werden konnte, wird wiederverwendet: kein zweiter (kostenpflichtiger) OCR-Lauf
  with state_lock:
    cached = results.get(file)
  if cached and cached[0] == mtime:
    _, data, rel = cached
    log("'{}': OCR-Ergebnis aus dem Zwischenspeicher, nur noch ablegen".format(filename))
  else:
    with open(file, 'rb') as f:
      original = f.read()

    result = None
    for attempt in range(1, OCR_RETRIES + 1):
      try:
        log("OCR '{}' (Versuch {}/{})".format(filename, attempt, OCR_RETRIES))
        result = call_ocr(original, filename, scan_date)
        break
      except OcrError as err:
        log("OCR fehlgeschlagen für '{}': {}".format(filename, err))
        if not err.retryable or attempt == OCR_RETRIES:
          break
        time.sleep(RETRY_DELAY)

    if result is None:
      rel, data = "{}_Fehler.pdf".format(mtime_str), original
      log("'{}' wird ohne OCR abgelegt: {}".format(filename, rel))
    else:
      data, meta = result
      rel = safe_relpath(meta.get('path')) if isinstance(meta, dict) else None
      if rel is None:
        rel = "_Unbekannt/{}.pdf".format(mtime_str)
        log("Keine brauchbaren Metadaten für '{}' - ablegen als {}".format(filename, rel))
    with state_lock:
      results[file] = (mtime, data, rel)

  final = None
  for attempt in range(1, DELIVERY_RETRIES + 1):
    try:
      final = deliver(remoteMount, rel, data)
      break
    except OSError as err:
      log("Ablegen von '{}' als '{}' fehlgeschlagen (Versuch {}/{}): {}".format(filename, rel, attempt, DELIVERY_RETRIES, err))
      if attempt < DELIVERY_RETRIES:
        time.sleep(RETRY_DELAY)
  if final is None:
    with state_lock:
      cooldown[file] = time.time() + COOLDOWN_SECONDS
    log("'{}' bleibt in der Freigabe, nächster Versuch in {} s".format(filename, COOLDOWN_SECONDS))
    return

  log("Abgelegt: '{}' -> '{}'".format(file, final))
  with state_lock:
    results.pop(file, None)
    cooldown.pop(file, None)
  try:
    if os.path.getmtime(file) != mtime:
      log("'{}' wurde während der Verarbeitung geändert und bleibt in der Freigabe".format(filename))
    else:
      os.remove(file)
  except FileNotFoundError:
    pass
  except OSError as err:
    log("Original '{}' nicht gelöscht: {}".format(filename, err))


def run_worker(file, remoteMount):
  try:
    process_pdf(file, remoteMount)
  except Exception as err:  # ein Fehler darf den Watcher nie beenden
    log("Unerwarteter Fehler bei '{}': {}".format(file, err))
  finally:
    with state_lock:
      inflight.discard(file)


# ---------------------------------------------------------------------------
# Hauptschleife
# ---------------------------------------------------------------------------

def forget_stale_results():
  """Zwischengespeicherte Ergebnisse verwerfen, deren Datei nicht mehr in der Freigabe liegt."""
  with state_lock:
    for file in [f for f in results if not os.path.exists(f)]:
      results.pop(file, None)
      cooldown.pop(file, None)


def main():
  print('File watcher started' + (' (OCR: {}, Typ: {})'.format(OCR_URL, OCR_TYPE) if OCR_URL else ''), flush=True)
  executor = ThreadPoolExecutor(max_workers=OCR_WORKERS) if OCR_URL else None

  while True:
    i = 0
    while True:
      i = i + 1
      shareEnable = os.getenv('PROXY{}_ENABLE'.format(i))
      if shareEnable == None:
        break
      elif not shareEnable == "1":
        continue

      shareDirectory = '/share{}'.format(i)
      remoteMount = '/remote{}'.format(i)

      files = glob.glob(shareDirectory + '/*.*')
      for file in files:
        _, filename = os.path.split(file)

        try:
          mtime = os.path.getmtime(file)
        except OSError:
          continue
        if time.time() - mtime < SETTLE_SECONDS:
          log("Waiting for changes in File: '" + filename + "'")
          continue

        if executor is None or not is_pdf(file):
          move_legacy(file, remoteMount)
          continue

        with state_lock:
          if file in inflight or cooldown.get(file, 0) > time.time():
            continue
          inflight.add(file)
        executor.submit(run_worker, file, remoteMount)
    forget_stale_results()
    time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
  main()
