#!/usr/bin/env bash
# Integrationstest des OCR-Ablaufs (Stub-OCR, keine Kosten). Braucht Docker mit Compose.
#   bash test/run.sh          Stack starten, Testdateien per SMB1 hochladen, Ergebnis im SMB2-Ziel prüfen
#   KEEP=1 bash test/run.sh   Stack danach nicht abbauen
set -u
cd "$(dirname "$0")"
DC="docker compose -f docker-compose.test.yml"
fail=0
ok()   { echo "  ok   $*"; }
bad()  { echo "  FAIL $*"; fail=1; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

cleanup() { [ "${KEEP:-0}" = "1" ] || $DC --profile tools down -v --remove-orphans >/dev/null 2>&1; }
trap cleanup EXIT

echo "== Stack bauen und starten"
$DC --profile tools down -v --remove-orphans >/dev/null 2>&1
$DC up -d --build >/dev/null 2>&1 || { echo "Start fehlgeschlagen"; $DC logs | tail -30; exit 1; }
for i in $(seq 1 60); do
  $DC logs smb1proxy 2>&1 | grep -q "File watcher started" && break
  sleep 1
done
$DC logs smb1proxy 2>&1 | grep -q "File watcher started" || { echo "Proxy nicht bereit"; $DC logs smb1proxy | tail -30; exit 1; }

echo "== Dateien per SMB1 hochladen"
$DC --profile tools run --rm client -c '
  cd /tmp
  for n in ok1 ok2 umlaut low nometa badpath err500 err400 slow1 slow2; do
    printf "%%PDF-1.4\nORIGINAL %s\n%%%%EOF\n" "$n" > "$n.pdf"
  done
  echo "kein pdf" > fake.pdf
  echo "notiz" > note.txt
  cmds=""
  for f in ok1.pdf ok2.pdf umlaut.pdf low.pdf nometa.pdf badpath.pdf err500.pdf err400.pdf slow1.pdf slow2.pdf fake.pdf note.txt; do
    cmds="$cmds put $f;"
    [ "$f" = "ok1.pdf" ] && cmds="$cmds !sleep 1;"   # ok1 und ok2 verschieden alt, sonst gleiche Namen
  done
  smbclient //smb1proxy/scanshare -U scanuser%secret1 -m NT1 --option="client min protocol=NT1" -c "$cmds"
' >/dev/null 2>&1 || { bad "SMB1-Upload"; $DC logs smb1proxy | tail -20; }

echo "== Auf Verarbeitung warten (max. 90 s)"
for i in $(seq 1 90); do
  n=$($DC exec -T smb1proxy sh -c 'ls /share1 2>/dev/null | wc -l' 2>/dev/null | tr -d '\r ')
  [ "${n:-1}" = "0" ] && break
  sleep 1
done
sleep 2

LIST=$($DC exec -T target sh -c 'cd /target && find . -type f | sort' 2>/dev/null | tr -d '\r')
echo "== Inhalt des SMB2-Ziels"; echo "$LIST" | sed 's/^/     /'

echo "== Prüfungen"
has() { echo "$LIST" | grep -qE "$1"; }
count() { echo "$LIST" | grep -cE "$1"; }
check "Freigabe des Proxys ist leer"                       '[ "$($DC exec -T smb1proxy sh -c "ls /share1 | wc -l" | tr -d "\r ")" = "0" ]'
check "Normal: Korrespondent/Datum_Inhalt.pdf"             'has "^\./Telekom/2026-09-18_Rechnung-Mobilfunk\.pdf$"'
check "Kollision wird nummeriert statt überschrieben (_2)" '[ "$(count "^\./Telekom/2026-09-18_Rechnung-Mobilfunk(_[0-9]+)?\.pdf$")" -ge 4 ] && has "_2\.pdf$"'
check "Umlaute/Leerzeichen im Ordnernamen"                 'has "^\./Müller & Söhne/2026-09-18_Kündigung\.pdf$"'
check "Unsicher: _Pruefen"                                 'has "^\./_Pruefen/2026-09-18_Stadtwerke_Abrechnung\.pdf$"'
check "Ohne Metadaten und Pfad mit ../: _Unbekannt (2x)"   '[ "$(count "^\./_Unbekannt/[0-9]{8}_[0-9]{6}(_[0-9]+)?\.pdf$")" = "2" ]'
check "OCR-Fehler 500 und 400: _Fehler (2x)"               '[ "$(count "^\./[0-9]{8}_[0-9]{6}_Fehler(_[0-9]+)?\.pdf$")" = "2" ]'
check "Keine Datei außerhalb des Ziels (kein evil.pdf)"    '! has "evil"'
check "Keine .part-Reste"                                  '! has "\.part$"'
check "Nicht-PDF wie bisher: <Zeitstempel>.txt"            'has "^\./[0-9]{8}_[0-9]{6}\.txt$"'
check "Datei ohne %PDF-Kopf wie bisher: <Zeitstempel>.pdf" 'has "^\./[0-9]{8}_[0-9]{6}\.pdf$"'
check "OCR-Ergebnis liegt im Ziel (Stub-Inhalt)"           '$DC exec -T target sh -c "grep -rl OCR-STUB /target | wc -l" | tr -d "\r " | grep -qE "^[0-9]+$" && [ "$($DC exec -T target sh -c "grep -rl OCR-STUB /target | wc -l" | tr -d "\r ")" -ge 7 ]'
check "_Fehler enthält das Original (ohne OCR)"            '[ "$($DC exec -T target sh -c "grep -l ORIGINAL /target/*_Fehler*.pdf | wc -l" | tr -d "\r ")" = "2" ]'

LOGS=$($DC logs ocrstub 2>&1)
check "err500: 2 Versuche (Wiederholung)"                  '[ "$(echo "$LOGS" | grep -c "\"file\": \"err500.pdf\"")" = "2" ]'
check "err400: 1 Versuch (endgültig, keine Wiederholung)"  '[ "$(echo "$LOGS" | grep -c "\"file\": \"err400.pdf\"")" = "1" ]'
check "Jede erfolgreiche Datei nur 1x an OCR (kein Doppel)" '[ "$(echo "$LOGS" | grep -c "\"file\": \"slow1.pdf\"")" = "1" ]'
check "Anfrage: llm/meta/lenient/scan_date + API-Key"      'echo "$LOGS" | grep "\"file\": \"ok1.pdf\"" | grep -q "\"llm\": \"true\".*\"meta\": \"true\".*\"lenient\": \"true\"" && echo "$LOGS" | grep "\"file\": \"ok1.pdf\"" | grep -qE "\"scan_date\": \"[0-9]{4}-[0-9]{2}-[0-9]{2}\"" && echo "$LOGS" | grep "\"file\": \"ok1.pdf\"" | grep -q "\"apikey\": \"testkey\""'

echo
if [ "$fail" = "0" ]; then echo "ALLE TESTS BESTANDEN"; else echo "FEHLER (siehe oben)"; $DC logs smb1proxy | tail -40; fi
exit $fail
