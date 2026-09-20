#!/usr/bin/env python3
"""Unit-Tests für die OCR-Kette im Watcher, ohne Docker, Netz und Kosten.

  python3 test/test_watcher.py
"""

import importlib.util, os, sys, tempfile, time, unittest
from unittest import mock

os.environ.update({'OCR_URL': 'http://ocr.invalid', 'RETRY_DELAY': '0', 'OCR_RETRIES': '2', 'DELIVERY_RETRIES': '2'})
spec = importlib.util.spec_from_file_location('watcher', os.path.join(os.path.dirname(__file__), '..', 'watcher.py'))
watcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watcher)  # __main__-Schutz: startet die Schleife nicht

PDF = b'%PDF-1.4\nscan\n%%EOF\n'
META = {'path': 'Telekom/2026-09-18_Rechnung.pdf'}


class Case(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.share = os.path.join(self.tmp.name, 'share')
    self.remote = os.path.join(self.tmp.name, 'remote')
    os.makedirs(self.share)
    os.makedirs(self.remote)
    self.file = os.path.join(self.share, 'scan.pdf')
    with open(self.file, 'wb') as f:
      f.write(PDF)
    watcher.results.clear()
    watcher.cooldown.clear()
    watcher.inflight.clear()

  def tearDown(self):
    self.tmp.cleanup()

  def files(self):
    return sorted(os.path.relpath(os.path.join(d, n), self.remote) for d, _, ns in os.walk(self.remote) for n in ns)

  def test_ok(self):
    with mock.patch.object(watcher, 'call_ocr', return_value=(PDF + b'ocr', META)) as ocr:
      watcher.process_pdf(self.file, self.remote)
    self.assertEqual(ocr.call_count, 1)
    self.assertEqual(self.files(), ['Telekom/2026-09-18_Rechnung.pdf'])
    self.assertFalse(os.path.exists(self.file), 'Original erst nach erfolgreicher Ablage gelöscht')
    self.assertEqual(watcher.results, {})

  def test_delivery_failure_does_not_repeat_ocr(self):
    """Regression: nach einem Ablagefehler darf die Datei nicht ein zweites Mal durch OCR (kostet Geld)."""
    real_deliver = watcher.deliver
    fail = {'n': 10}

    def flaky(remote, rel, data):
      if fail['n'] > 0:
        fail['n'] -= 1
        raise OSError(112, 'Host is down')
      return real_deliver(remote, rel, data)

    with mock.patch.object(watcher, 'call_ocr', return_value=(PDF + b'ocr', META)) as ocr, \
         mock.patch.object(watcher, 'deliver', side_effect=flaky):
      watcher.process_pdf(self.file, self.remote)          # Ziel down: alle Versuche scheitern
      self.assertTrue(os.path.exists(self.file), 'Datei bleibt in der Freigabe')
      self.assertIn(self.file, watcher.cooldown)
      self.assertIn(self.file, watcher.results)
      self.assertEqual(ocr.call_count, 1)
      fail['n'] = 0                                          # Ziel wieder da
      watcher.cooldown.clear()
      watcher.process_pdf(self.file, self.remote)
    self.assertEqual(ocr.call_count, 1, 'kein zweiter OCR-Lauf')
    self.assertEqual(self.files(), ['Telekom/2026-09-18_Rechnung.pdf'])
    self.assertFalse(os.path.exists(self.file))
    self.assertEqual(watcher.results, {})
    self.assertEqual(watcher.cooldown, {})

  def test_changed_file_is_processed_again(self):
    with mock.patch.object(watcher, 'call_ocr', return_value=(PDF + b'ocr', META)) as ocr, \
         mock.patch.object(watcher, 'deliver', side_effect=OSError('down')):
      watcher.process_pdf(self.file, self.remote)
    old = os.path.getmtime(self.file)
    os.utime(self.file, (old + 100, old + 100))              # Scanner hat die Datei neu geschrieben
    with mock.patch.object(watcher, 'call_ocr', return_value=(PDF + b'ocr2', META)) as ocr2:
      watcher.process_pdf(self.file, self.remote)
    self.assertEqual(ocr2.call_count, 1, 'geänderte Datei => neuer OCR-Lauf')

  def test_ocr_permanent_failure_files_original_as_fehler(self):
    err = watcher.OcrError('HTTP 401', False)
    with mock.patch.object(watcher, 'call_ocr', side_effect=err) as ocr:
      watcher.process_pdf(self.file, self.remote)
    self.assertEqual(ocr.call_count, 1, 'nicht wiederholbarer Fehler: ein Versuch')
    (name,) = self.files()
    self.assertTrue(name.endswith('_Fehler.pdf'), name)
    with open(os.path.join(self.remote, name), 'rb') as f:
      self.assertEqual(f.read(), PDF)

  def test_ocr_retryable_failure_retries_then_fehler(self):
    with mock.patch.object(watcher, 'call_ocr', side_effect=watcher.OcrError('HTTP 500', True)) as ocr:
      watcher.process_pdf(self.file, self.remote)
    self.assertEqual(ocr.call_count, 2)
    self.assertTrue(self.files()[0].endswith('_Fehler.pdf'))

  def test_missing_or_evil_meta_goes_to_unbekannt(self):
    for meta in (None, {'path': '../../etc/x.pdf'}, {'path': 'a/b/c.pdf'}, {'path': '/abs.pdf'}):
      with self.subTest(meta=meta):
        with open(self.file, 'wb') as f:
          f.write(PDF)
        with mock.patch.object(watcher, 'call_ocr', return_value=(PDF + b'ocr', meta)):
          watcher.process_pdf(self.file, self.remote)
        self.assertTrue(any(n.startswith('_Unbekannt/') for n in self.files()), self.files())
        for n in self.files():
          self.assertNotIn('..', n)

  def test_collisions_are_numbered(self):
    for _ in range(3):
      with open(self.file, 'wb') as f:
        f.write(PDF)
      with mock.patch.object(watcher, 'call_ocr', return_value=(PDF + b'ocr', META)):
        watcher.process_pdf(self.file, self.remote)
    self.assertEqual(self.files(), ['Telekom/2026-09-18_Rechnung.pdf', 'Telekom/2026-09-18_Rechnung_2.pdf', 'Telekom/2026-09-18_Rechnung_3.pdf'])

  def test_forget_stale_results(self):
    watcher.results['/weg/scan.pdf'] = (1.0, b'x', 'a/b.pdf')
    watcher.cooldown['/weg/scan.pdf'] = time.time() + 100
    watcher.forget_stale_results()
    self.assertEqual(watcher.results, {})
    self.assertEqual(watcher.cooldown, {})

  def test_safe_relpath(self):
    ok = ['Telekom/2026-09-18_Rechnung.pdf', 'Müller & Söhne/x.PDF']
    bad = ['x.pdf', 'a/b/c.pdf', '../x.pdf', 'a/../x.pdf', '/x.pdf', 'a\\b.pdf', '.hidden/x.pdf', 'a/.x.pdf', 'a/b.txt', '', None, 5]
    for p in ok:
      self.assertEqual(watcher.safe_relpath(p), p)
    for p in bad:
      self.assertIsNone(watcher.safe_relpath(p), repr(p))


if __name__ == '__main__':
  unittest.main(verbosity=2)
