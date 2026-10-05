#!/usr/bin/env python3
"""Локальный ENOT-мост: HTTP-прокси к Yonote мимо VPN-туннеля.

Зачем: ENOT (app.yonote.ru / anewera.yonote.ru) стоит в России и отбивает
зарубежные адреса. На машине Андрея весь исходящий трафик забирает туннель
neko-tun, поэтому запросы из WSL и от Hermes-агентов рвутся на TLS-рукопожатии.
Читалка (sync_yonote.py + netpath.py) решает это привязкой сокета к домашнему
адресу 192.168.1.140 — но только со стороны Windows.

Мост делает то же самое, но как сервис: слушает HTTP на 192.168.1.140:8788,
каждый POST /api/<method> пересылает в ENOT через netpath-выход (с bind),
токен берёт из заголовка Authorization клиента (свой ключ не хранит).

Клиент (агент Hermes) настраивается просто:
    api_url: http://192.168.1.140:8788/api
    и передаёт свой Authorization: Bearer <ключ> как обычно.

Запуск:  python enot_bridge.py            (или батником «Запустить ЕНОТ-мост.bat»)
Остановка: Ctrl+C в окне моста.
"""
import http.server
import json
import socket
import sys
import urllib.error
import urllib.request

import netpath
import settings

PORT = 8788
API_HOST = 'anewera.yonote.ru'          # куда ходим (см. config.json: yonote_url)
API = 'https://' + API_HOST + '/api'

UPSTREAM_TIMEOUT = 45


def call_enot(method, payload, auth_header):
    """Один RPC-вызов ENOT через прямой выход (netpath.opener_for)."""
    body = json.dumps(payload or {}, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        f'{API}/{method}', data=body, method='POST',
        headers={'Authorization': auth_header,
                 'Content-Type': 'application/json',
                 'Accept': 'application/json'})
    opener, via = netpath.opener_for(API_HOST, settings.load())
    try:
        with opener.open(req, timeout=UPSTREAM_TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        detail = b''
        try:
            detail = e.read()
        except Exception:
            pass
        return e.code, detail


class BridgeHandler(http.server.BaseHTTPRequestHandler):
    """POST /api/<method> -> ENOT; GET /health -> статус моста."""

    def log_message(self, format, *args):
        sys.stderr.write('%s - %s\n' % (self.address_string(), format % args))

    def _reply(self, code, payload, ctype='application/json'):
        data = payload if isinstance(payload, bytes) else json.dumps(
            payload, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/health':
            cfg = settings.load()
            addr = netpath.pick(API_HOST, cfg) or '(системный маршрут)'
            self._reply(200, {'ok': True, 'upstream': API, 'via': addr})
            return
        self._reply(404, {'ok': False, 'error': 'not_found'})

    def do_POST(self):
        if not self.path.startswith('/api/'):
            self._reply(404, {'ok': False, 'error': 'not_found'})
            return
        method = self.path[len('/api/'):].strip('/')
        if not method or '/' in method:
            self._reply(400, {'ok': False, 'error': 'bad_method'})
            return

        length = int(self.headers.get('Content-Length') or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b'{}')
        except json.JSONDecodeError:
            self._reply(400, {'ok': False, 'error': 'bad_json'})
            return

        auth = self.headers.get('Authorization') or ''
        if not auth:
            self._reply(401, {'ok': False, 'error': 'no_auth'})
            return

        code, data = call_enot(method, payload, auth)
        if code == 200:
            self._reply(200, data)
        elif code in (400, 401, 403, 404, 429):
            self._reply(code, data)
        else:
            # 5xx и сетевые обрывы не отдавали тела — формируем своё
            if not data:
                data = json.dumps(
                    {'ok': False, 'error': f'upstream_{code}',
                     'hint': 'ENOT недоступен; проверьте, запущен ли мост '
                             'и работает ли прямой выход (netpath)'}).encode('utf-8')
            self._reply(502, data)


class BridgeServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # слушаем на домашнем адресе: он виден и из WSL (mirrored), и с Windows
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        http.server.ThreadingHTTPServer.server_bind(self)


def main():
    cfg = settings.load()
    host = str(cfg.get('bridge_host') or '192.168.1.140')
    srv = BridgeServer((host, PORT), BridgeHandler)
    via = netpath.pick(API_HOST, cfg) or '(системный маршрут)'
    print(f'ENOT-мост слушает http://{host}:{PORT}  (вверх: {API}, выход: {via})')
    print('Агентам: api_url = http://%s:%d/api + свой Authorization: Bearer' % (host, PORT))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\nмост остановлен')


if __name__ == '__main__':
    main()
