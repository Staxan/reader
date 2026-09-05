#!/usr/bin/env python3
"""Базы документов: какие есть у читалки и какая открыта сейчас.

База — это отдельная папка с документами и всё, что к ней относится: версии,
замечания, привязка агентов. Базы между собой не пересекаются. Это главное
правило и оно держится не на памяти, а на проверках: новая папка не может
лежать внутри уже подключённой базы, а загрузка не идёт в папку, где уже есть
документы.

Формат bases.json (в корне Orchestra, рядом с config.json):

    {
     "current": "yonote",
     "bases": [
      {"id": "yonote",
       "name": "Yonote",
       "path": "C:\\Users\\andrn\\Desktop\\Yonote",
       "source": "enot",              откуда пришла: enot | backup | local
       "site": "https://anewera.yonote.ru",
       "read_only": false,            true — выгрузка из этой базы запрещена
       "created": "2026-09-05",
       "loaded": "2026-09-05",        когда прошла первая загрузка
       "docs": 780}
     ]
    }

Файл в репозиторий не попадает: в нём пути конкретной машины.

Если bases.json нет, список собирается из поля `base` в config.json — читалка
работает как раньше, до появления баз, и ничего настраивать не нужно.
"""
import json
import os
import re
from datetime import date

import settings

REG_NAME = 'bases.json'
REG_PATH = os.path.join(settings.ROOT, REG_NAME)

SOURCES = ('enot', 'backup', 'local')


def reg_path():
    return REG_PATH


# ------------------------------------------------------------------ чтение


def _clean(b):
    """Приводит запись к известным полям. Путь переводится под текущую систему."""
    return {
        'id': str(b.get('id') or '').strip(),
        'name': str(b.get('name') or '').strip(),
        'path': settings.translate(str(b.get('path') or '').strip()),
        'source': str(b.get('source') or 'local').strip(),
        'site': str(b.get('site') or '').strip(),
        'read_only': bool(b.get('read_only', False)),
        'created': str(b.get('created') or '').strip(),
        'loaded': str(b.get('loaded') or '').strip(),
        'docs': int(b.get('docs') or 0),
    }


def _config_base():
    """Поле `base` прямо из config.json.

    Читаем файл сами, а не через settings.load(): тот спрашивает открытую базу
    у нас, и вызов замкнулся бы в круг. Нужно, чтобы первая запись в списке
    появилась сама — на машине, где bases.json ещё нет.
    """
    try:
        with open(settings.CONFIG, encoding='utf-8') as f:
            return settings.translate((json.load(f) or {}).get('base') or '')
    except (OSError, ValueError):
        return ''


def load(cfg_base=''):
    """Список баз. -> {'current': id, 'bases': [...]}.

    Когда bases.json ещё нет, список собирается из поля `base` в config.json:
    читалка работает как до появления баз, настраивать ничего не нужно.
    """
    data = {'current': '', 'bases': []}
    if os.path.exists(REG_PATH):
        try:
            with open(REG_PATH, encoding='utf-8') as f:
                raw = json.load(f) or {}
            if isinstance(raw, list):
                raw = {'bases': raw}
            data['current'] = str(raw.get('current') or '')
            data['bases'] = [_clean(b) for b in (raw.get('bases') or [])
                             if isinstance(b, dict) and b.get('id')]
        except (OSError, ValueError) as e:
            print(f'{REG_NAME} не прочитался ({e}), беру базу из config.json')

    if not data['bases']:
        first = settings.translate(cfg_base or '') or _config_base()
        if first:
            name = os.path.basename(first.rstrip('/\\')) or 'База'
            data['bases'] = [_clean({
                'id': make_id(name, data),
                'name': name,
                'path': first,
                'source': 'local',
                'site': '',
                'created': '',
            })]
            data['current'] = data['bases'][0]['id']

    ids = [b['id'] for b in data['bases']]
    if data['current'] not in ids:
        data['current'] = ids[0] if ids else ''
    return data


def save(data):
    keep = [_clean(b) for b in (data.get('bases') or []) if b.get('id')]
    out = {'current': str(data.get('current') or ''), 'bases': keep}
    tmp = REG_PATH + '.part'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, REG_PATH)
    return out


def get(base_id, data=None):
    if not base_id:
        return None
    data = data if data is not None else load()
    for b in data['bases']:
        if b['id'] == base_id:
            return b
    return None


def current(data=None):
    data = data if data is not None else load()
    return get(data.get('current'), data) or (data['bases'][0] if data['bases'] else None)


def current_path(cfg_base=''):
    """Путь текущей базы. Пусто — значит баз нет вовсе."""
    b = current(load(cfg_base))
    return b['path'] if b else ''


def make_id(name, data=None):
    """Из имени — короткий латинский id, свободный в списке."""
    translit = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'h', 'ц': 'c', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
        'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
    }
    base = ''.join(translit.get(ch, ch) for ch in (name or '').lower())
    base = re.sub(r'[^a-z0-9]+', '-', base).strip('-') or 'base'
    data = data if data is not None else load()
    used = {b['id'] for b in data['bases']}
    if base not in used:
        return base
    n = 2
    while f'{base}-{n}' in used:
        n += 1
    return f'{base}-{n}'


# ------------------------------------------------------------- проверки


def norm(path):
    return os.path.normcase(os.path.abspath(settings.translate(path or '')))


def inside(a, b):
    """Лежит ли путь a внутри b (или это один и тот же путь).\n"""
    a, b = norm(a), norm(b)
    return a == b or a.startswith(b.rstrip(os.sep) + os.sep)


def count_docs(path):
    """Сколько .md в папке. Только имена, файлы не читаются."""
    n = 0
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            n += sum(1 for f in filenames if f.endswith('.md'))
    except OSError:
        pass
    return n


def has_docs(path):
    """Есть ли в папке хоть один документ. Дешевле полного пересчёта."""
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            for f in filenames:
                if f.endswith('.md'):
                    return True
    except OSError:
        pass
    return False


def check_new(name, path, data=None):
    """Можно ли завести такую базу. -> ошибка или пустая строка.

    Тут собраны все запреты, ради которых база и заводится отдельно:
    имя не повторяется, папка не пересекается с чужой базой и не содержит
    готовых документов. Иначе первая загрузка легла бы поверх уже собранного.
    """
    data = data if data is not None else load()
    name = (name or '').strip()
    path = (path or '').strip()
    if not name:
        return 'нужно имя базы'
    if not path:
        return 'нужно указать папку базы'
    if any(b['name'].lower() == name.lower() for b in data['bases']):
        return f'база с именем «{name}» уже есть — имена должны различаться'

    for b in data['bases']:
        if not b['path']:
            continue
        if inside(path, b['path']):
            return (f'эта папка лежит внутри базы «{b["name"]}» — базы не должны '
                    f'пересекаться')
        if inside(b['path'], path):
            return (f'внутри этой папки лежит база «{b["name"]}» — базы не должны '
                    f'пересекаться')

    if os.path.exists(path) and not os.path.isdir(path):
        return 'по этому пути лежит файл, а не папка'
    if has_docs(path):
        return ('в папке уже есть документы — база загружается один раз и не '
                'перезаписывается. Укажите пустую папку')
    parent = os.path.dirname(os.path.abspath(settings.translate(path)))
    if parent and not os.path.isdir(parent):
        return f'нет папки, в которой предлагается создать базу: {parent}'
    return ''


# ------------------------------------------------------------- изменения


def add(name, path, source='enot', site='', read_only=True, data=None):
    """Заводит базу. -> (запись, ошибка). Файлы не создаёт."""
    data = data if data is not None else load()
    err = check_new(name, path, data)
    if err:
        return None, err
    entry = _clean({
        'id': make_id(name, data),
        'name': name.strip(),
        'path': path.strip(),
        'source': source if source in SOURCES else 'local',
        'site': site,
        'read_only': read_only,
        'created': date.today().isoformat(),
    })
    data['bases'].append(entry)
    if not data.get('current'):
        data['current'] = entry['id']
    save(data)
    return entry, ''


def update(base_id, **fields):
    """Меняет поля базы. -> (запись, ошибка)."""
    data = load()
    b = get(base_id, data)
    if not b:
        return None, 'база не найдена'
    for k, v in fields.items():
        if k in ('id', 'path'):
            continue                      # id и путь не меняются
        b[k] = v
    save(data)
    return b, ''


def set_current(base_id):
    data = load()
    b = get(base_id, data)
    if not b:
        return None, 'база не найдена'
    if not os.path.isdir(b['path']):
        return None, f'нет папки базы: {b["path"]}'
    data['current'] = base_id
    save(data)
    return b, ''


def forget(base_id):
    """Убирает базу из списка. Файлы остаются на диске.

    Удалять документы читалка не умеет намеренно: список — это указатель,
    а не хранилище. Потерять главы из-за нажатия в интерфейсе нельзя.
    """
    data = load()
    b = get(base_id, data)
    if not b:
        return None, 'база не найдена'
    if data.get('current') == base_id and len(data['bases']) > 1:
        return None, ('это открытая база — сначала откройте другую')
    data['bases'] = [x for x in data['bases'] if x['id'] != base_id]
    if data.get('current') == base_id:
        data['current'] = data['bases'][0]['id'] if data['bases'] else ''
    save(data)
    return b, ''


def mark_loaded(base_id, docs):
    return update(base_id, loaded=date.today().isoformat(), docs=int(docs or 0))


def suggest_dir(data=None):
    """Где предложить создать новую базу: рядом с уже открытой."""
    data = data if data is not None else load()
    b = current(data)
    if b and b['path']:
        return os.path.dirname(os.path.abspath(b['path']))
    return os.path.join(os.path.expanduser('~'), 'Desktop')


if __name__ == '__main__':
    cfg = settings.load()
    d = load(cfg.get('base'))
    print(f'{REG_NAME}:', REG_PATH, '(есть)' if os.path.exists(REG_PATH) else '(нет)')
    for b in d['bases']:
        mark = '→' if b['id'] == d['current'] else ' '
        state = 'нет папки' if not os.path.isdir(b['path']) else f'{count_docs(b["path"])} док.'
        ro = ', только чтение' if b['read_only'] else ''
        print(f' {mark} {b["id"]:<12} {b["name"]:<22} {state:<12} {b["source"]}{ro}')
        print(f'   {b["path"]}')
    if not d['bases']:
        print('баз нет: укажите папку в config.json полем "base"')
