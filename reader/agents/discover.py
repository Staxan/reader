#!/usr/bin/env python3
"""Поиск агентов Hermes на этой машине.

Профили Hermes лежат в `~/.hermes/profiles/<имя>/config.yaml`, и там уже есть
всё нужное для связи — порт и секрет webhook:

    platforms:
      webhook:
        enabled: true
        extra:
          host: 127.0.0.1
          port: 8644
          secret: ...

Подписки лежат рядом, в `webhook_subscriptions.json`. Из имени подписки и
порта собирается адрес, на который читалка отправляет задание.

Читаем без PyYAML: в Windows его обычно нет, а тащить зависимость в читалку,
которая живёт на чистой стандартной библиотеке, того не стоит. Нужен один
вложенный блок известной формы — разбирается по отступам.
"""
import json
import os
import re

HOME_CANDIDATES = ('~/.hermes', '/home/andrn/.hermes')

# «Ника», а не «nika-redaktor»: в интерфейсе нужно человеческое имя.
KNOWN_NAMES = {
    'nika-redaktor': 'Ника',
    'nika': 'Ника',
    'hefest': 'Гефест',
    'gefest': 'Гефест',
    'mira': 'Мира',
    'tim': 'Тим',
    'gera': 'Гера',
    'default': 'Гера',
}


def hermes_root():
    """Папка Hermes на этой машине или пусто."""
    for c in HOME_CANDIDATES:
        p = os.path.expanduser(c)
        if os.path.isdir(p):
            return p
    return ''


def _yaml_webhook(path):
    """Блок platforms.webhook из config.yaml. -> dict или None.

    Разбор по отступам, без PyYAML: ищем `platforms:`, внутри `webhook:`,
    внутри — простые пары ключ-значение и вложенный `extra:`.
    """
    try:
        with open(path, encoding='utf-8') as f:
            lines = f.read().splitlines()
    except OSError:
        return None

    def indent(s):
        return len(s) - len(s.lstrip(' '))

    i, n = 0, len(lines)
    while i < n and lines[i].rstrip() != 'platforms:':
        i += 1
    if i >= n:
        return None
    base = indent(lines[i])
    i += 1

    # ищем webhook: внутри platforms
    wh_at, wh_ind = None, None
    while i < n:
        ln = lines[i]
        if ln.strip() and indent(ln) <= base:
            break
        if ln.strip() == 'webhook:':
            wh_at, wh_ind = i, indent(ln)
            break
        i += 1
    if wh_at is None:
        return None

    out, extra = {}, {}
    i = wh_at + 1
    cur = out
    extra_ind = None
    kv = re.compile(r'^\s*([A-Za-z_][\w-]*)\s*:\s*(.*?)\s*$')
    while i < n:
        ln = lines[i]
        if ln.strip() and indent(ln) <= wh_ind:
            break
        m = kv.match(ln)
        if m:
            key, val = m.group(1), m.group(2)
            if key == 'extra' and not val:
                cur, extra_ind = extra, indent(ln)
            elif extra_ind is not None and indent(ln) <= extra_ind:
                cur = out
                cur[key] = val
            else:
                cur[key] = val
        i += 1

    def truthy(v):
        return str(v).strip().strip('"\'').lower() in ('true', 'yes', '1', 'on')

    def clean(v):
        return str(v).strip().strip('"\'')

    port = clean(extra.get('port') or out.get('port') or '')
    return {
        'enabled': truthy(out.get('enabled', 'false')),
        'host': clean(extra.get('host') or '127.0.0.1'),
        'port': int(port) if port.isdigit() else 0,
        'secret': clean(extra.get('secret') or ''),
    }


def _subscriptions(profile_dir):
    """Имена подписок webhook профиля и их секреты."""
    p = os.path.join(profile_dir, 'webhook_subscriptions.json')
    try:
        with open(p, encoding='utf-8') as f:
            data = json.load(f) or {}
    except (OSError, ValueError):
        return {}
    out = {}
    for name, sub in data.items():
        if isinstance(sub, dict):
            out[name] = (sub.get('secret') or '').strip()
    return out


def _pick_route(subs):
    """Какая подписка отвечает за книгу.

    Сначала ищем по названию (book, kniga, читалка), потом берём первую —
    у профиля обычно одна подписка, и угадывать особо не из чего.
    """
    if not subs:
        return '', ''
    for name in subs:
        low = name.lower()
        if 'book' in low or 'kniga' in low or 'чит' in low or 'книг' in low:
            return name, subs[name]
    first = next(iter(subs))
    return first, subs[first]


def human_name(profile):
    if profile in KNOWN_NAMES:
        return KNOWN_NAMES[profile]
    base = profile.split('-')[0]
    return KNOWN_NAMES.get(base, base.capitalize())


def scan():
    """Все агенты Hermes на машине. -> список записей.

    Каждая запись:
        profile   имя профиля (у Геры — 'default', он живёт в корне)
        name      человеческое имя
        state     ready | no-webhook | no-route
        url       адрес подписки, если она есть
        secret    секрет подписки или общий секрет платформы
        port      порт webhook
        note      что не так, если state не ready
    """
    root = hermes_root()
    if not root:
        return []

    found = []

    def one(profile, cfg_path, prof_dir):
        wh = _yaml_webhook(cfg_path)
        subs = _subscriptions(prof_dir)
        route, route_secret = _pick_route(subs)

        rec = {
            'profile': profile,
            'name': human_name(profile),
            'kind': 'hermes-webhook',
            'url': '',
            'secret': '',
            'port': (wh or {}).get('port') or 0,
            'route': route,
            'state': 'no-webhook',
            'note': '',
        }

        if not wh or not wh.get('enabled') or not wh.get('port'):
            rec['note'] = 'webhook в профиле выключен'
            found.append(rec)
            return

        host = wh['host'] if wh['host'] not in ('0.0.0.0', '') else '127.0.0.1'
        rec['secret'] = route_secret or wh.get('secret') or ''
        if not route:
            rec['state'] = 'no-route'
            rec['note'] = 'webhook включён, но подписки на книгу нет'
            found.append(rec)
            return

        rec['url'] = f'http://{host}:{wh["port"]}/webhooks/{route}'
        rec['state'] = 'ready'
        found.append(rec)

    # профили
    pdir = os.path.join(root, 'profiles')
    if os.path.isdir(pdir):
        for name in sorted(os.listdir(pdir)):
            d = os.path.join(pdir, name)
            cfg = os.path.join(d, 'config.yaml')
            if os.path.isdir(d) and os.path.exists(cfg):
                one(name, cfg, d)

    # корневой профиль — это тоже агент (у Андрея там Гера)
    root_cfg = os.path.join(root, 'config.yaml')
    if os.path.exists(root_cfg):
        one('default', root_cfg, root)

    return found


def as_agents(existing_ids=()):
    """Найденные профили в форме записей реестра.

    Совпадения по профилю отсеиваются: если агент уже добавлен, второй раз
    предлагать его не нужно.
    """
    import agents.registry as registry

    out = []
    for f in scan():
        aid = registry.make_id(f['name'])
        out.append({
            'id': aid,
            'name': f['name'],
            'kind': 'hermes-webhook',
            'profile': f['profile'],
            'url': f['url'],
            'secret': f['secret'],
            'enabled': f['state'] == 'ready',
            'state': f['state'],
            'note': f['note'],
            'port': f['port'],
            'route': f['route'],
        })
    return out


if __name__ == '__main__':
    root = hermes_root()
    print('Hermes:', root or 'не найден')
    for f in scan():
        line = f'  {f["name"]:<10} {f["profile"]:<16} {f["state"]}'
        if f['url']:
            line += f'  {f["url"]}'
        if f['note']:
            line += f'  — {f["note"]}'
        print(line)
