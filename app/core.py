"""Local Sakuya application and authenticated browser settings. Standard library only."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
import subprocess
from pathlib import Path
import secrets
import sys
import threading
import time
from urllib.parse import urlsplit
from common import APP_ID, APP_NAME, APP_VERSION, atomic_json
import startup
import updater
import cloudru
from release_stage import HEX40, stage_release, verify_staged_release
from recovery import arm_verified_update, recover

ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/assets/app.css': ('app.css', 'text/css; charset=utf-8'),
          '/assets/app.js': ('app.js', 'text/javascript; charset=utf-8'),
          '/favicon.svg': ('favicon.svg', 'image/svg+xml')}


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def create_server(data: Path, identity: str, session: str, token: str,
                  stop: threading.Event, settings: dict, logger, runtime=None):
    csrf = secrets.token_urlsafe(32)
    settings_lock = threading.RLock()
    startup_lock = threading.Lock()
    update_lock = threading.Lock()
    cloud_lock = threading.Lock()
    started = time.monotonic()
    runtime = runtime if runtime is not None else {'mode': 'normal', 'tray': 'unavailable'}

    def snapshot():
        with settings_lock:
            current = {key: settings[key] for key in ('port', 'autostart_browser')}
        return {'app': APP_NAME, 'version': APP_VERSION, 'session': session, 'pid': os.getpid(),
                'ready': not stop.is_set(), 'url': f'http://127.0.0.1:{server.server_port}',
                'uptime_seconds': int(time.monotonic() - started), 'mode': runtime.get('mode', 'normal'),
                'tray': runtime.get('tray', 'unavailable'), 'python_version': sys.version.split()[0],
                'python_executable': sys.executable, 'data_directory': str(data),
                'settings': current, 'startup': startup.status(),
                'capabilities': {'open_folder': os.name == 'nt', 'open_log': os.name == 'nt', 'open_documents': os.name == 'nt'}, 'update': {'repository': 'Aspksa/Sakuya', 'automatic_install': False, 'manual_restart_install': True, 'recovery_available': True}}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'

        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def reply(self, code, value, content_type='application/json; charset=utf-8'):
            body = json.dumps(value, ensure_ascii=False).encode() if not isinstance(value, bytes) else value
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def do_GET(self):
            if not self.valid_host():
                return self.reply(403, {'error': 'Недопустимый адрес запроса'})
            path = urlsplit(self.path).path
            try:
                if path == '/api/health':
                    return self.reply(200, {'app': APP_NAME, 'app_id': APP_ID, 'instance_id': identity,
                                           'session': session, 'pid': os.getpid(), 'ready': not stop.is_set()})
                if path in ASSETS:
                    file, mime = ASSETS[path]
                    return self.reply(200, (Path(__file__).parent / file).read_bytes(), mime)
                if path == '/api/bootstrap':
                    return self.reply(200, dict(snapshot(), csrf=csrf))
                if path == '/api/status':
                    return self.reply(200, snapshot())
                if path == '/api/update/check':
                    return self.reply(200, updater.check_remote())
                if path == '/api/update/status':
                    return self.reply(200, {'repository': 'Aspksa/Sakuya', 'automatic_install': False,
                                            'recovery_available': True, 'message': 'Доступна подготовка обновления. Для применения нужен перезапуск Sakuya'})
                if path == '/api/cloudru/status':
                    config = cloudru.get_config(data)
                    return self.reply(200, {'connected': cloudru.has_key(data), 'model': config['model'],
                                            'provider': 'Cloud.ru Foundation Models'})
                if path == '/api/cloudru/models':
                    if not cloudru.has_key(data):
                        return self.reply(400, {'error': 'Сначала сохраните API-ключ'})
                    models = cloudru.fetch_models(data)
                    return self.reply(200, {'models': models, 'connected': True})
                if path == '/api/tools':
                    return self.reply(200, {'actions': ['open-data', 'open-documents', 'open-log'] if os.name == 'nt' else [],
                                            'arbitrary_commands': False})
                if path == '/api/logs':
                    log = data / 'logs/sakuya.log'
                    if log.exists():
                        with log.open('rb') as stream:
                            stream.seek(max(0, log.stat().st_size - 32768))
                            text = stream.read().decode('utf-8', errors='replace')
                        lines = text.splitlines()[-80:]
                    else:
                        lines = []
                    return self.reply(200, {'lines': lines, 'path': str(log)})
                self.reply(404, {'error': 'Страница не найдена'})
            except Exception:
                logger.exception('Ошибка чтения локального интерфейса')
                self.reply(500, {'error': 'Не удалось прочитать данные. Подробности — в журнале Sakuya.'})

        def read_body(self):
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                raise ValueError('Ожидается JSON-запрос')
            if self.headers.get('Transfer-Encoding'):
                raise ValueError('Неподдерживаемый формат запроса')
            length = int(self.headers.get('Content-Length', '-1'))
            if not 0 <= length <= 4096:
                raise ValueError('Недопустимый размер запроса')
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError('Запрос передан не полностью')
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ValueError('Ожидается объект с настройками')
            return result

        def do_POST(self):
            if not self.valid_host():
                return self.reply(403, {'error': 'Недопустимый адрес запроса'})
            if self.path == '/api/shutdown':
                # Native tray authorization is separate from browser sessions.
                if (self.headers.get('Origin') is not None or
                        not hmac.compare_digest(self.headers.get('Authorization', '').encode(), ('Bearer ' + token).encode())):
                    return self.reply(403, {'error': 'Forbidden'})
                self.reply(200, {'stopping': True})
                stop.set()
                return
            expected_origin = f'http://127.0.0.1:{self.server.server_port}'
            if (self.headers.get('Origin') != expected_origin or
                    self.headers.get('Sec-Fetch-Site', 'same-origin') != 'same-origin' or
                    not hmac.compare_digest(self.headers.get('X-Sakuya-CSRF', '').encode(), csrf.encode())):
                return self.reply(403, {'error': 'Сессия недействительна. Обновите страницу и повторите действие.'})
            if stop.is_set():
                return self.reply(503, {'error': 'Sakuya завершается. Дождитесь следующего запуска.'})
            try:
                payload = self.read_body()
                if self.path == '/api/cloudru/key':
                    if set(payload) != {'key'} or not isinstance(payload['key'], str):
                        raise ValueError('Необходимо указать API-ключ')
                    if os.name != 'nt':
                        return self.reply(501, {'error': 'Защищённое хранение ключей доступно в Windows'})
                    with cloud_lock:
                        cloudru.store_key(data, payload['key'])
                    logger.info('Ключ Cloud.ru сохранён в защищённом локальном хранилище')
                    return self.reply(200, {'saved': True})
                if self.path == '/api/cloudru/delete':
                    if payload != {'confirm': True}:
                        raise ValueError('Нужно подтвердить удаление ключа')
                    with cloud_lock:
                        cloudru.delete_key(data)
                    logger.info('Ключ Cloud.ru удалён')
                    return self.reply(200, {'deleted': True})
                if self.path == '/api/cloudru/model':
                    if set(payload) != {'model'}:
                        raise ValueError('Укажите модель')
                    with cloud_lock:
                        cloudru.set_model(data, payload['model'])
                    return self.reply(200, {'model': payload['model']})
                if self.path == '/api/update/restart':
                    if payload != {'confirm': True}:
                        raise ValueError('Требуется подтверждение перезапуска')
                    if os.name != 'nt':
                        return self.reply(501, {'error': 'Перезапуск через сайт доступен в Windows'})
                    root = Path(os.environ.get('SAKUYA_ROOT', Path(__file__).resolve().parents[1])).resolve()
                    journal = root / '.updates' / 'transaction.json'
                    if not journal.is_file():
                        return self.reply(409, {'error': 'Нет подготовленного обновления'})
                    transaction = json.loads(journal.read_text(encoding='utf-8'))
                    if transaction.get('phase') != 'armed':
                        return self.reply(409, {'error': 'Обновление не ожидает запуска'})
                    subprocess.Popen([sys.executable, '-X', 'utf8', str(root / 'scripts/restart-worker.py'),
                                      '--owner-pid', str(os.getpid())],
                                     cwd=str(root), env=dict(os.environ, SAKUYA_ROOT=str(root)),
                                     creationflags=subprocess.CREATE_NO_WINDOW,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self.reply(200, {'restarting': True})
                    stop.set()
                    return
                if self.path == '/api/update/prepare':
                    if set(payload) != {'commit'} or not isinstance(payload['commit'], str) or not HEX40.fullmatch(payload['commit']):
                        raise ValueError('Укажите полный SHA коммита GitHub')
                    if not update_lock.acquire(blocking=False):
                        return self.reply(409, {'error': 'Другое обновление уже подготавливается'})
                    try:
                        commit_id = payload['commit']
                        # Browser-supplied commit must match GitHub's current main branch.
                        remote = updater.check_remote()
                        if not remote.get('reachable') or remote.get('commit') != commit_id:
                            return self.reply(409, {'error': 'Коммит больше не соответствует GitHub main'})
                        root = Path(os.environ.get('SAKUYA_ROOT', Path(__file__).resolve().parents[1])).resolve()
                        release = root / 'releases' / commit_id
                        if not release.exists():
                            stage_release(root, commit_id)
                        verify_staged_release(root, commit_id)
                        # Supervisor owns the exclusive InstanceLock while HTTP is active.
                        recover(root)
                        arm_verified_update(root, commit_id)
                        logger.info('Подготовлено обновление %s. Требуется перезапуск.', commit_id)
                        return self.reply(200, {'prepared': True, 'commit': commit_id, 'restart_required': True})
                    finally:
                        update_lock.release()
                if self.path == '/api/settings':
                    if set(payload) != {'port', 'autostart_browser'}:
                        raise ValueError('Нужны поля port и autostart_browser')
                    if type(payload['port']) is not int or not 0 <= payload['port'] <= 65535:
                        raise ValueError('Порт должен быть целым числом от 0 до 65535')
                    if type(payload['autostart_browser']) is not bool:
                        raise ValueError('Настройка браузера должна быть true или false')
                    with settings_lock:
                        updated = dict(settings, **payload)
                        save(data, updated)
                        settings.clear()
                        settings.update(updated)
                    logger.info('Настройки запуска сохранены')
                    return self.reply(200, {'settings': {key: updated[key] for key in ('port', 'autostart_browser')}, 'restart_required': True})
                if self.path == '/api/startup':
                    if set(payload) != {'enabled'} or type(payload['enabled']) is not bool:
                        raise ValueError('Нужно поле enabled: true или false')
                    if os.name != 'nt':
                        return self.reply(501, {'error': 'Автозапуск доступен в Windows'})
                    with startup_lock:
                        startup.configure('enable' if payload['enabled'] else 'disable', quiet=True)
                        result = startup.status()
                    logger.info('Автозапуск Windows: %s', result['enabled'])
                    return self.reply(200, {'startup': result})
                if self.path == '/api/open':
                    target = payload.get('target')
                    if set(payload) != {'target'} or type(target) is not str or target not in ('data', 'documents', 'log'):
                        raise ValueError('Неизвестная папка или файл')
                    if os.name != 'nt':
                        return self.reply(501, {'error': 'Открытие папки и файла доступно в Windows'})
                    path = {'data': data, 'documents': data / 'documents', 'log': data / 'logs/sakuya.log'}[target]
                    if target == 'documents':
                        path.mkdir(parents=True, exist_ok=True)
                    if not path.exists():
                        return self.reply(404, {'error': 'Файл или папка отсутствует'})
                    os.startfile(str(path))
                    return self.reply(200, {'opened': target})
                self.reply(404, {'error': 'Действие не найдено'})
            except (ValueError, UnicodeError) as exc:
                self.reply(400, {'error': str(exc)})
            except Exception:
                logger.exception('Ошибка действия в локальном интерфейсе')
                self.reply(500, {'error': 'Не удалось выполнить действие. Проверьте доступ к файлам и журнал Sakuya.'})

        def log_message(self, fmt, *args):
            logger.debug('HTTP ' + fmt, *args)

    preferred = settings.get('port', 0)
    if type(preferred) is not int or not 0 <= preferred <= 65535:
        raise ValueError('Некорректный порт в settings.json')
    try:
        server = LocalServer(('127.0.0.1', preferred), Handler)
    except OSError as exc:
        import errno
        if not preferred or (exc.errno != errno.EADDRINUSE and getattr(exc, 'winerror', None) != 10048):
            raise
        logger.info('Порт %s занят, выбираю свободный', preferred)
        server = LocalServer(('127.0.0.1', 0), Handler)
    server.settings_lock = settings_lock
    server.runtime = runtime
    return server


def save(data: Path, settings: dict):
    atomic_json(data / 'settings.json', settings)
