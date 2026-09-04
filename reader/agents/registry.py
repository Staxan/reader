#!/usr/bin/env python3
"""Реестр агентов: кто подключён к читалке и как с ним связаться.

Раньше адрес агента лежал в config.json одним полем `hermes_webhook`. Пока
агент был один, этого хватало. Реестр решает ту же задачу для нескольких:
каждый агент — запись в agents.json, а не строка в коде.

Формат agents.json (в корне Orchestra, рядом с config.json):

    {
     "default": "nika",
     "agents": [
      {"id": "nika", "name": "Ника", "kind": "hermes-webhook",
       "profile": "nika-redaktor",
       "url": "http://127.0.0.1:8644/webhooks/nika-book",
       "secret": "...", "enabled": true}
     ]
    }

Файл в репозиторий не попадает: в нём адреса и секреты конкретной машины.

Если agents.json нет, реестр собирается из старых полей config.json — читалка
работает как раньше, без правки настроек.
"""
import json
import os
import re

import settings

REG_NAME = 'agents.json'
REG_PATH = os.path.join(settings.ROOT, REG_NAME)

KINDS = ('hermes-webhook', 'cli')


def reg_path():
    return REG_PATH


# --------------------------------------------------------------- чтение


def _from_config(cfg):
    """Один агент из старых полей config.json — совместимость с прежней версией."""
    url = (cfg.get('hermes_webhook') or '').strip()
    if not url:
        return None
    return {
        'id': 'nika',
        'name': 'Ника',
        'kind': 'hermes-webhook',
        'profile': 'nika-redaktor',
        'url': url,
        'secret': (cfg.get('hermes_secret') or '').strip(),
        'enabled': True,
    }


def reveal(value):
    """Разворачивает ссылку на секрет в само значение.

    В agents.json секрет хранится не сам, а ссылкой: `env:AGENT_SECRET_NIKA`.
    Так реестр можно открыть, скопировать на другую машину и показать кому
    угодно — ключа в нём нет. Само значение лежит в `.env` рядом с config.json,
    и `.env` целиком в .gitignore.

    Старая форма (секрет прямо в поле) продолжает работать: ломать рабочую
    установку из-за смены формата нельзя.
    """
    v = str(value or '').strip()
    if v.startswith('env:'):
        return settings.env_get(v[4:].strip())
    return v


def secret_of(agent):
    """Значение секрета агента, откуда бы оно ни бралось."""
    return reveal((agent or {}).get('secret'))


def stash_secret(agent_id, value):
    """Кладёт секрет в .env и возвращает ссылку на него для реестра.

    Пустое значение ссылку не создаёт: у CLI-агентов секрета нет вообще.
    """
    value = str(value or '').strip()
    if not value:
        return ''
    if value.startswith('env:'):
        return value
    name = settings.env_name_for(agent_id)
    settings.env_set(name, value)
    return f'env:{name}'



def load(cfg=None):
    """Реестр целиком: {'default': id, 'agents': [...]}."""
    cfg = cfg if cfg is not None else settings.load()
    data = {'default': '', 'agents': []}
    if os.path.exists(REG_PATH):
        try:
            with open(REG_PATH, encoding='utf-8') as f:
                raw = json.load(f) or {}
            if isinstance(raw, list):          # допускаем короткую форму
                raw = {'agents': raw}
            data['default'] = str(raw.get('default') or '')
            data['agents'] = [a for a in (raw.get('agents') or [])
                              if isinstance(a, dict) and a.get('id')]
        except (OSError, ValueError) as e:
            print(f'{REG_NAME} не прочитался ({e}), беру настройки из config.json')

    if not data['agents']:
        one = _from_config(cfg)
        if one:
            data['agents'] = [one]
            data['default'] = one['id']

    ids = [a['id'] for a in data['agents'] if a.get('enabled', True)]
    if data['default'] not in ids:
        data['default'] = ids[0] if ids else ''
    return data


def save(data):
    """Пишет реестр. Сохраняет только известные поля, порядок не меняет.

    Секреты по пути наружу превращаются в ссылки `env:ИМЯ`, а значения уходят
    в `.env`. Это делается здесь, а не только при добавлении агента: иначе
    секрет, пришедший из старого config.json, останется лежать в реестре
    открытым текстом — и попадёт в репозиторий при первой невнимательности.
    """
    keep = []
    for a in data.get('agents') or []:
        if not a.get('id'):
            continue
        aid = str(a['id'])
        keep.append({
            'id': aid,
            'name': str(a.get('name') or aid),
            'kind': str(a.get('kind') or 'hermes-webhook'),
            'profile': str(a.get('profile') or ''),
            'url': str(a.get('url') or ''),
            'secret': stash_secret(aid, a.get('secret')),
            'command': str(a.get('command') or ''),
            'enabled': bool(a.get('enabled', True)),
        })
    out = {'default': str(data.get('default') or ''), 'agents': keep}
    tmp = REG_PATH + '.part'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, REG_PATH)
    try:
        os.chmod(REG_PATH, 0o600)
    except OSError:
        pass
    return out


def enabled(data=None):
    data = data if data is not None else load()
    return [a for a in data['agents'] if a.get('enabled', True)]


def get(agent_id, data=None):
    if not agent_id:
        return None
    data = data if data is not None else load()
    for a in data['agents']:
        if a['id'] == agent_id:
            return a
    return None


def default_agent(data=None):
    data = data if data is not None else load()
    return get(data.get('default'), data) or (enabled(data) or [None])[0]


def name_of(agent_id, data=None):
    a = get(agent_id, data)
    return a['name'] if a else (agent_id or '')


def upsert(agent, data=None):
    """Добавляет агента или обновляет существующего по id.

    Секрет по пути в реестр превращается в ссылку `env:ИМЯ`, а само значение
    уходит в `.env`. Поэтому реестр остаётся файлом без ключей.
    """
    data = data if data is not None else load()
    aid = str(agent.get('id') or '').strip()
    if not aid:
        return data, 'у агента нет id'

    agent = dict(agent)
    if 'secret' in agent:
        agent['secret'] = stash_secret(aid, agent.get('secret'))
        if not agent['secret']:
            # пустое поле не должно стирать уже сохранённую ссылку:
            # форму можно отправить, не вводя секрет заново
            old = get(aid, data)
            if old and old.get('secret'):
                agent['secret'] = old['secret']

    for i, a in enumerate(data['agents']):
        if a['id'] == aid:
            merged = dict(a)
            merged.update(agent)
            data['agents'][i] = merged
            break
    else:
        data['agents'].append(agent)
    if not data.get('default'):
        data['default'] = aid
    return save(data), None


def remove(agent_id, data=None):
    data = data if data is not None else load()
    data['agents'] = [a for a in data['agents'] if a['id'] != agent_id]
    if data.get('default') == agent_id:
        data['default'] = ''
    return save(data), None


def set_enabled(agent_id, on, data=None):
    data = data if data is not None else load()
    a = get(agent_id, data)
    if not a:
        return data, 'агент не найден'
    a['enabled'] = bool(on)
    return save(data), None


def set_default(agent_id, data=None):
    data = data if data is not None else load()
    if agent_id and not get(agent_id, data):
        return data, 'агент не найден'
    data['default'] = agent_id or ''
    return save(data), None


def make_id(name, data=None):
    """Из человеческого имени — короткий латинский id, свободный в реестре."""
    translit = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'h', 'ц': 'c', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
        'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
    }
    base = ''.join(translit.get(ch, ch) for ch in (name or '').lower())
    base = re.sub(r'[^a-z0-9]+', '-', base).strip('-') or 'agent'
    data = data if data is not None else load()
    used = {a['id'] for a in data['agents']}
    if base not in used:
        return base
    n = 2
    while f'{base}-{n}' in used:
        n += 1
    return f'{base}-{n}'
