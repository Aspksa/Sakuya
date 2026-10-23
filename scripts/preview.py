"""Internal browser QA entry; Sites supervisor owns its lifetime. No user data used."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bootstrap'))
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import logging
import os
import secrets
import tempfile
import threading

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('preview_core', root / 'app/core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

with tempfile.TemporaryDirectory(prefix='sakuya-preview-') as directory:
    data = Path(directory)
    os.environ['SAKUYA_DATA_DIR'] = directory
    (data / 'documents').mkdir()
    (data / 'logs').mkdir()
    logger = logging.getLogger('preview')
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(data / 'logs/sakuya.log', encoding='utf-8')
    logger.addHandler(handler)
    logger.info('Sakuya: проверка интерфейса запущена')
    settings = {'port': 0, 'autostart_browser': False}
    core.save(data, settings)
    server = core.create_server(data, 'preview.sakuya', secrets.token_hex(16), secrets.token_urlsafe(32),
                                threading.Event(), settings, logger, runtime={'mode':'diagnostic','tray':'unavailable'})
    threading.Thread(target=server.serve_forever, daemon=True).start()
    address = f'127.0.0.1:{server.server_port}'

    class Proxy(BaseHTTPRequestHandler):
        def forward(self):
            if self.headers.get('Host') not in ('terminal.local:4173', 'localhost:4173', '127.0.0.1:4173'):
                self.send_error(403)
                return
            headers = {key:value for key,value in self.headers.items() if key.lower() not in ('host','connection')}
            if 'Origin' in headers:
                if headers['Origin'] not in ('http://terminal.local:4173','http://127.0.0.1:4173','http://localhost:4173'):
                    self.send_error(403)
                    return
                headers['Origin'] = 'http://' + address
            headers['Host'] = address
            length = int(self.headers.get('Content-Length','0'))
            if length > 4096:
                self.send_error(413)
                return
            body = self.rfile.read(length) if length else None
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=20)
            try:
                connection.request(self.command,self.path,body,headers)
                result = connection.getresponse()
                content = result.read()
                self.send_response(result.status)
                for key,value in result.getheaders():
                    if key.lower() not in ('server','date','connection'):
                        self.send_header(key,value)
                self.end_headers()
                self.wfile.write(content)
            finally:
                connection.close()
        do_GET = forward
        do_POST = forward
        def log_message(self,*args):
            pass

    proxy = ThreadingHTTPServer(('0.0.0.0',4173), Proxy)
    print('Sakuya QA preview: http://terminal.local:4173/', flush=True)
    try:
        proxy.serve_forever()
    finally:
        server.shutdown()
        server.server_close()
        proxy.server_close()
