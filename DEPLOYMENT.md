# Betrieb mit OCR-Kette (Deployment)

Anleitung, den smb1-proxy zusammen mit dem [pdf-adobe-ocr](https://github.com/thomasblankschein/pdf-adobe-ocr)-Service (Version 2.1.0) auf einer VM zu betreiben:

```
Scanner --SMB1--> smb1proxy --HTTP--> pdf-adobe-ocr (Adobe OCR + LLM)
                     \--SMB2--> Zielverzeichnis  (<Korrespondent>/<Datum>_<Inhalt>.pdf)
```

Die Reihenfolge ist so gewählt, dass jederzeit ein Rückweg auf das alte Verhalten besteht (siehe [Rückweg](#6-rückweg)).

## 0. Vorbereitung

- **Zugangsdaten:** Adobe PDF Services (Client ID und Client Secret aus der Adobe Developer Console) und ein Anthropic-API-Key.
- **Internet:** Die VM muss ausgehend per HTTPS Adobe und Anthropic erreichen.
- **Sicherung** der bisherigen Compose-Datei (auf der VM, neben der Datei):

  ```bash
  cp docker-compose.yml docker-compose.yml.vor-ocr
  ```

- **Neuer Proxy-Code:** `master` dieses Repos holen und das Image neu bauen (`git pull` bzw. dein bisheriger Weg).

## 1. Zugangsdaten anlegen

Einen gemeinsamen API-Key erzeugen, mit dem der Proxy den OCR-Service anspricht:

```bash
openssl rand -hex 24
```

Neben der Compose-Datei zwei Dateien anlegen, beide mit `chmod 600` und **nicht** einchecken.

`.env` (nur für die Variablen-Substitution in der Compose-Datei):

```env
OCR_API_KEY=<der erzeugte Key>
```

`.env.ocr` (Konfiguration des OCR-Service):

```env
PDF_SERVICES_CLIENT_ID=...
PDF_SERVICES_CLIENT_SECRET=...
LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-5
ANTHROPIC_API_KEY=...
OWN_NAMES=Vorname Nachname; Straße 1, 12345 Ort; mail@example.org
LOG_LEVEL=info
```

`LLM_MODEL` unbedingt setzen: ohne Angabe gilt bei Anthropic `claude-opus-5`, das ist teurer. `OWN_NAMES` (durch `;` getrennt) sind Name, Adresse und Mailadresse des Empfängers; damit erkennt das Modell den *Absender* eines Briefs statt des Empfängers. Alle Einstellungen des Service stehen in dessen [README](https://github.com/thomasblankschein/pdf-adobe-ocr#konfiguration).

## 2. Compose-Datei ergänzen

Die bisherige `smb1proxy`-Konfiguration bleibt unverändert. Ergänzt wird nur Folgendes (das vollständige Muster liegt als [`docker-compose.ocr.example.yml`](docker-compose.ocr.example.yml) im Repo):

```yaml
services:
  smb1proxy:
    # ... alles wie bisher, zusätzlich:
    depends_on:
      - pdf-adobe-ocr
    environment:
      OCR_URL: http://pdf-adobe-ocr:3000
      OCR_API_KEY: ${OCR_API_KEY}

  pdf-adobe-ocr:
    build: https://github.com/thomasblankschein/pdf-adobe-ocr.git#v2.1.0
    env_file: .env.ocr
    environment:
      PORT: "3000"
      API_KEY: ${OCR_API_KEY}
      TZ: Europe/Berlin
    restart: unless-stopped
    # bewusst keine "ports": nur der Proxy im Compose-Netz erreicht den Service
```

Hinweise:

- Scheitert der Build aus der Git-URL auf der VM (nicht getestet), das Repo daneben klonen und `build: ../pdf-adobe-ocr` verwenden.
- **Empfohlen (nicht getestet):** ein Volume für die Freigabe, damit ein gerade hochgeladener Scan ein Neuerstellen des Containers übersteht. Beim `smb1proxy` `volumes: [scanspool:/share1]` ergänzen und unter `volumes:` `scanspool:` deklarieren (für weitere Freigaben entsprechend `/share2` usw.). Ohne Volume liegt `/share1` im Dateisystem des Containers: es übersteht `docker restart`, aber nicht `docker compose up` nach einer Änderung.

## 3. Starten und prüfen

```bash
docker compose up -d --build
```

```bash
docker compose logs --tail 20
```

Erwartet:

- OCR-Service: `service gestartet … llm=anthropic/claude-sonnet-5`
- Proxy: `File watcher started (OCR: http://pdf-adobe-ocr:3000)`

Antwortet das NAS beim Start noch nicht, beendet sich der Proxy beim Mounten (bekanntes Verhalten des Entrypoints); `restart: unless-stopped` holt den Start nach.

## 4. Erster Test

Einen **harmlosen** Scan (keine sensiblen Daten) vom Scanner abschicken, denn das Dokument geht an Adobe und Anthropic. Im Log mitlesen:

```bash
docker compose logs -f smb1proxy
```

Zeitverlauf: etwa 30 s Wartezeit, bis die Datei als fertig gilt, dann 20 bis 30 s OCR. Nach rund einer Minute steht `Abgelegt: … -> …/<Korrespondent>/<Datum>_<Inhalt>.pdf` im Log, und die Datei liegt im Zielverzeichnis. Im PDF-Viewer prüfen, ob die Suche Text findet.

## 5. Was die Ergebnisse bedeuten

| Ergebnis im Ziel | Bedeutung | Was tun |
|---|---|---|
| `<Korrespondent>/<Datum>_<Inhalt>.pdf` | normal | nichts |
| `_Unbekannt/…` | Absender nicht erkannt | bei Bedarf von Hand einsortieren |
| `_Pruefen/…` | Modell war unsicher oder die Seite kaum lesbar (Datum ist dann das Scandatum) | Name und Datum prüfen |
| `<Zeitstempel>_Fehler.pdf` im Zielordner | OCR endgültig gescheitert; es ist das Original ohne OCR | Ursache im Log suchen, Datei später von Hand erneut einliefern |
| `<Zeitstempel>.<Endung>` | Datei war keine PDF, wurde wie früher nur umbenannt | – |

Ein falscher `OCR_API_KEY` wird nicht wiederholt und erzeugt **still** lauter `_Fehler`-Dateien; in den ersten Tagen darauf achten. Kein Scan geht verloren: das Original wird erst gelöscht, wenn das Ergebnis im Ziel liegt.

## 6. Rückweg

`OCR_URL` in der Compose-Datei leer setzen und neu starten; der Proxy benennt dann wieder wie früher nach Änderungszeit um:

```bash
docker compose up -d
```

## 7. Betrieb

- **Kosten:** Pro PDF fällt ein Adobe-Aufruf plus etwa zwei Claude-Aufrufe an (Korrektur und Metadaten). Das Adobe-Kontingent in der Developer Console im Blick behalten und bei Anthropic ein Ausgabenlimit setzen.
- **Updates des OCR-Service:** Tag im Compose ändern und `docker compose up -d --build`, möglichst wenn kein Scan unterwegs ist. Der Proxy überbrückt etwa 4 Minuten Ausfall (5 Versuche im Abstand von 60 s, einstellbar mit `OCR_RETRIES` und `RETRY_DELAY`); danach gibt es `_Fehler`-Dateien, die nicht automatisch nachverarbeitet werden.
- **Ausfall des Ziels:** Der Proxy wartet, die Datei bleibt in der Freigabe, und das fertige OCR-Ergebnis wird zwischengespeichert, sodass nach der Rückkehr des Ziels nicht erneut (kostenpflichtig) verarbeitet wird.
- **Datenschutz:** Jede eingescannte PDF geht an Adobe und, mit `LLM_PROVIDER`, an Anthropic. Ohne `OCR_URL` bleibt alles lokal.
- **Große Dokumente:** Über 50 Seiten gibt es nur Adobe-OCR (ohne LLM-Korrektur); änderbar mit `LLM_MAX_PAGES` in `.env.ocr`.
- **Formate:** JPEG und TIFF laufen unverändert durch wie bisher; nur PDFs werden per OCR verarbeitet. Eine PDF gilt als **ein** Dokument, mehrere Briefe in einer Datei werden nicht getrennt.
- **Sehr schlechte Scans** (stark verblasst): Bei unbrauchbarer Adobe-Textebene transkribiert das Modell die Seite selbst, schreibt aber nur sichere Zeilen. Unleserliche Seiten bekommen keinen Text und landen in `_Pruefen`.
- **Sicherheit:** Der OCR-Service wird nicht nach außen veröffentlicht und ist zusätzlich per `API_KEY` geschützt. SMB1 sollte weiterhin nur im Scanner-Netz erreichbar sein.

## Nicht getestet

Der echte Scanner (bisher nur ein SMB1-Client als Ersatz), der Build aus der Git-URL auf der VM, das Volume auf `/share1` und OpenAI-Modelle. Deshalb der vorsichtige erste Test mit einem einzelnen, unkritischen Scan.

## Weiterführend

- [README](README.md): alle Einstellungen des Proxys und die Ablageregeln.
- [test/](test/): Unit-, Integrations- und Echtdienst-Tests (`bash test/run.sh`, `python3 test/test_watcher.py`).
