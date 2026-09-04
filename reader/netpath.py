#!/usr/bin/env python3
"""Выход к ENOT мимо туннеля VPN.

ENOT стоит на российском сервере и отбивает зарубежные адреса: соединение
рвётся на TLS-рукопожатии, будто сервер недоступен. На машине Андрея поднят
`sing-tun` (адаптер neko-tun), который забирает весь трафик, — поэтому запрос
уходит через туннель и не доходит.

Проверено на живом соединении:

    без привязки:                FAIL TimeoutError (handshake) — 12.0 с
    привязка к 192.168.1.140:    OK   HTTP/1.1 200 OK          — 0.2 с

Отсюда решение: привязывать исходящий сокет к домашнему адресу. Тогда запрос
идёт обычной сетью, а VPN остаётся включённым и не мешает работе с зарубежными
сервисами.

Касается только запросов к ENOT: всё остальное ходит как раньше.

Ограничение: адрес вида 192.168.1.140 существует на стороне Windows. При
запуске из WSL трафик всё равно уходит через хост в туннель, и привязка не
поможет — там выгрузка останется недоступной, о чём сообщение говорит прямо.
"""
import http.client
import ipaddress
import socket
import urllib.request

CACHE = {'addr': None, 'checked': False}


def local_addresses():
    """Локальные адреса машины, кроме адресов туннеля и служебных сетей.

    Порядок важен: сначала обычные домашние сети (192.168.*, 10.*), потом
    остальное. Адреса WSL (172.17.*) и туннеля (172.19.*) отбрасываем — через
    них запрос как раз и уходит не туда.
    """
    found = set()
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass

    # то же через «пробный» сокет: покажет адрес, выбранный системой
    for probe in ('8.8.8.8', '84.201.174.160'):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((probe, 53))
            found.add(s.getsockname()[0])
        except OSError:
            pass
        finally:
            s.close()

    def rank(a):
        try:
            ip = ipaddress.ip_address(a)
        except ValueError:
            return (9, a)
        if a.startswith('192.168.'):
            return (0, a)
        if a.startswith('10.'):
            return (1, a)
        if a.startswith(('172.17.', '172.18.', '172.19.', '172.20.')):
            return (8, a)          # WSL и туннели — в конец
        if ip.is_loopback or ip.is_link_local:
            return (9, a)
        return (2, a)

    return sorted(found, key=rank)


def probe(addr, host, port=443, timeout=6):
    """Проходит ли TLS до host с этого адреса."""
    import ssl

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        if addr:
            s.bind((addr, 0))
        s.connect((host, port))
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(s, server_hostname=host):
            return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def pick(host, cfg=None):
    """Какой адрес использовать. -> адрес или '' (как раньше).

    'auto' — перебор с проверкой и запоминанием: перебирать на каждом запросе
    дорого, а сеть в течение сессии не меняется.
    """
    import settings

    cfg = cfg if cfg is not None else settings.load()
    want = str(cfg.get('direct_bind') or '').strip()

    if not want:
        return ''
    if want.lower() != 'auto':
        return want
    if CACHE['checked']:
        return CACHE['addr'] or ''

    CACHE['checked'] = True
    if probe('', host):                 # туннель не мешает — ничего не меняем
        CACHE['addr'] = ''
        return ''
    for addr in local_addresses():
        if addr.startswith(('127.', '172.17.', '172.18.', '172.19.')):
            continue
        if probe(addr, host):
            CACHE['addr'] = addr
            return addr
    CACHE['addr'] = ''
    return ''


def reset():
    CACHE['addr'] = None
    CACHE['checked'] = False


# ------------------------------------------------------- opener для urllib


class BoundHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS-соединение с привязкой к нужному локальному адресу."""

    bind_addr = None

    def __init__(self, *a, **kw):
        addr = kw.pop('bind_addr', None) or self.bind_addr
        super().__init__(*a, **kw)
        if addr:
            self.source_address = (addr, 0)


def opener_for(host, cfg=None):
    """urllib-opener, который ходит к host мимо туннеля и мимо прокси.

    Прокси отключаем всегда: Windows может держать системный прокси от VPN с
    пустым списком исключений, и тогда даже локальные запросы уходят в него.
    """
    addr = pick(host, cfg)
    if not addr:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({})), ''

    class Handler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            def build(h, **kw):
                kw['bind_addr'] = addr
                return BoundHTTPSConnection(h, **kw)
            return self.do_open(build, req)

    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), Handler()), addr


if __name__ == '__main__':
    import settings

    cfg = settings.load()
    host = 'app.yonote.ru'
    print('настройка direct_bind:', cfg.get('direct_bind') or '(пусто)')
    print('локальные адреса:', ', '.join(local_addresses()) or '(нет)')
    print('прямо, без привязки:', 'проходит' if probe('', host) else 'обрыв')
    addr = pick(host, cfg)
    print('выбран адрес:', addr or '(системный маршрут)')
    if addr:
        print('проверка с ним:', 'проходит' if probe(addr, host) else 'обрыв')
