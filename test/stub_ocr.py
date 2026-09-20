#!/usr/bin/env python3
"""Stub des pdf-adobe-ocr-Service für die Integrationstests (kein Adobe, kein LLM, keine Kosten).

Antwortet je nach Dateiname:
  err500*    -> HTTP 500 (Wiederholen)          err400* -> HTTP 400 (endgültig)
  nometa*    -> PDF ohne X-OCR-Meta             badpath* -> Pfad mit ../ (muss abgelehnt werden)
  low*       -> Pfad im Ordner _Pruefen         umlaut* -> Ordner "Müller & Söhne"
  slow*      -> antwortet erst nach 6 s         alles andere -> Telekom/2026-09-18_Rechnung-Mobilfunk.pdf
Jeder Aufruf wird als JSON-Zeile "REQUEST {...}" protokolliert.
"""

import base64, json, re, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
  def log_message(self, *args):
    pass

  def do_GET(self):
    self.send_response(200)
    self.end_headers()
    self.wfile.write(b'{"status":"ok"}')

  def do_POST(self):
    length = int(self.headers.get('Content-Length', '0'))
    body = self.rfile.read(length)
    ctype = self.headers.get('Content-Type', '')
    boundary = ctype.split('boundary=')[-1].encode()
    fields, filename, filedata = {}, '', b''
    for part in body.split(b'--' + boundary):
      if b'Content-Disposition' not in part:
        continue
      head, _, content = part.partition(b'\r\n\r\n')
      content = content[:-2] if content.endswith(b'\r\n') else content
      name = re.search(rb'name="([^"]*)"', head).group(1).decode()
      fn = re.search(rb'filename="([^"]*)"', head)
      if fn:
        filename, filedata = fn.group(1).decode(), content
      else:
        fields[name] = content.decode()
    print('REQUEST ' + json.dumps({'path': self.path, 'file': filename, 'bytes': len(filedata),
                                   'apikey': self.headers.get('X-API-Key'), 'fields': fields}), flush=True)

    low = filename.lower()
    if low.startswith('slow'):
      time.sleep(6)
    if low.startswith('err500'):
      return self.reply(500, b'{"error":"boom"}', 'application/json')
    if low.startswith('err400'):
      return self.reply(400, b'{"error":"bad input"}', 'application/json')

    pdf = b'%PDF-1.4\n% OCR-STUB ' + filename.encode() + b'\n%%EOF\n'
    headers = {}
    if not low.startswith('nometa'):
      path = 'Telekom/2026-09-18_Rechnung-Mobilfunk.pdf'
      if low.startswith('badpath'):
        path = '../../etc/evil.pdf'
      elif low.startswith('low'):
        path = '_Pruefen/2026-09-18_Stadtwerke_Abrechnung.pdf'
      elif low.startswith('umlaut'):
        path = 'Müller & Söhne/2026-09-18_Kündigung.pdf'
      meta = {'date': '2026-09-18', 'dateSource': 'document', 'correspondent': 'x', 'summary': 'x',
              'confidence': 'high', 'path': path}
      headers['X-OCR-Meta'] = base64.b64encode(json.dumps(meta).encode('utf-8')).decode()
    self.reply(200, pdf, 'application/pdf', headers)

  def reply(self, status, data, ctype, headers=None):
    self.send_response(status)
    self.send_header('Content-Type', ctype)
    self.send_header('Content-Length', str(len(data)))
    for k, v in (headers or {}).items():
      self.send_header(k, v)
    self.end_headers()
    self.wfile.write(data)


ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
