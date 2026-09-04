#!/usr/bin/env python3
"""Выгрузка документов из локальной базы в ENOT (Yonote).

Поток в одну сторону: файлы -> ENOT. Причина простая. Версии в базе
неизменяемы, а документ в ENOT правится в браузере в любой момент.
Двусторонняя синхронизация на таких правилах даёт конфликты, которые руками не
разобрать. Файлы — источник правды, ENOT — витрина.

В front-matter каждого файла уже лежат отметки прошлой выгрузки:

    yonote_id: 4d90066c-...      UUID документа в ENOT (пусто — ещё не создан)
    collection_id: 151db55a-...  UUID коллекции
    parent_id: ...               UUID родителя
    status: synced | draft
    synced_at: 2026-09-02
    synced_hash: d93cb314d925c275

По `synced_hash` видно, менялся ли файл после выгрузки, без обращения к сети.

Ключ доступа берётся из YONOTE_API_KEY в окружении или из поля `yonote_api_key`
в config.json. В репозиторий он не попадает.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

import netpath
import notes
import settings
import structure

API = 'https://app.yonote.ru/api'
API_HOST = 'app.yonote.ru'
TIMEOUT = 45
RETRIES = 3          # через VPN TLS до app.yonote.ru рвётся на ровном месте

FRONT_RE = re.compile(r'^---\r?\n(.*?)\r?\n---\r?\n?', re.S)
MD_LINK = re.compile(r'\[([^\]]+)\]\((https?://[^)]+)\)')


def api_key(cfg=None):
    key = (os.environ.get('YONOTE_API_KEY') or '').strip()
    if key:
        return key
    cfg = cfg if cfg is not None else settings.load()
    direct = (cfg.get('yonote_api_key') or '').strip()
    if direct:
        return direct
    return settings.env_get('YONOTE_API_KEY')


# ------------------------------------------------------------------ запрос


def call(method, payload=None, cfg=None):
    """RPC-вызов Yonote. -> (данные, ошибка).

    Через VPN Андрея соединение с app.yonote.ru рвётся на середине рукопожатия,
    поэтому повторяем несколько раз: без этого выгрузка падает случайным образом
    и выглядит как поломка кода.
    """
    key = api_key(cfg)
    if not key:
        return None, ('нет ключа доступа: добавьте YONOTE_API_KEY в окружение '
                      'или yonote_api_key в config.json')

    body = json.dumps(payload or {}, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        f'{API}/{method}', data=body, method='POST',
        headers={'Authorization': f'Bearer {key}',
                 'Content-Type': 'application/json',
                 'Accept': 'application/json'})

    # выход мимо туннеля VPN: ENOT отбивает зарубежные адреса
    opener, via = netpath.opener_for(API_HOST, cfg)

    last = ''
    for attempt in range(RETRIES):
        try:
            with opener.open(req, timeout=TIMEOUT) as r:
                return json.load(r), ''
        except urllib.error.HTTPError as e:
            detail = ''
            try:
                detail = e.read().decode('utf-8', 'replace')[:200]
            except Exception:
                pass
            if e.code in (401, 403):
                return None, f'доступ отклонён ({e.code}): проверьте ключ'
            if 400 <= e.code < 500:
                return None, f'ENOT ответил {e.code}: {detail}'
            last = f'ENOT ответил {e.code}: {detail}'
        except Exception as e:
            last = f'{type(e).__name__}: {e}'

    hint = ''
    if 'SSL' in last or 'EOF' in last or 'URLError' in last or 'timed out' in last:
        # ENOT стоит в России и отбивает зарубежные адреса. Со стороны это
        # выглядит как поломка кода, поэтому говорим прямо, что делать.
        hint = (' — ENOT не принимает зарубежные адреса. Запустите читалку со '
                'стороны Windows: там сработает прямой выход мимо VPN '
                '(direct_bind в config.json)')
        if via:
            hint = (f' — не прошло даже напрямую с адреса {via}; '
                    f'проверьте, доступен ли ENOT вообще')
    return None, f'связь с ENOT не установилась ({last}){hint}'


# --------------------------------------------------------- front-matter


def read_front(path):
    """-> (мета как dict, сырой блок front-matter, текст без него)."""
    with open(path, encoding='utf-8', errors='replace') as f:
        raw = f.read()
    m = FRONT_RE.match(raw)
    if not m:
        return {}, '', raw
    meta = {}
    for line in m.group(1).splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            meta[k.strip()] = v.strip()
    return meta, m.group(0), raw[m.end():]


def write_front(path, updates):
    """Меняет поля front-matter, не трогая текст документа."""
    meta, block, text = read_front(path)
    meta.update({k: str(v) for k, v in updates.items()})
    order = ['title', 'yonote_id', 'url_id', 'parent_id', 'collection_id',
             'kind', 'status', 'synced_at', 'synced_hash']
    keys = [k for k in order if k in meta] + [k for k in meta if k not in order]
    lines = '\n'.join(f'{k}: {meta[k]}' for k in keys)
    notes.atomic_write(path, f'---\n{lines}\n---\n{text}')
    return meta


def body_hash(path):
    """Отпечаток текста без front-matter: служебные поля на него не влияют."""
    _, _, text = read_front(path)
    return notes.text_hash(text.strip())


# ------------------------------------------------------------ подготовка


def prepare_text(text):
    """Готовит текст к выгрузке.

    Yonote срезает `[текст](url)` до анкера — ссылка перестаёт быть видимой.
    Прямые URL и метки сносок `[N]` он сохраняет. Поэтому markdown-ссылки
    разворачиваем в подпись плюс видимый адрес.
    """
    def unfold(m):
        label, url = m.group(1).strip(), m.group(2).strip()
        if label.rstrip('/') == url.rstrip('/'):
            return url
        return f'{label}: {url}'

    return MD_LINK.sub(unfold, text).strip() + '\n'


def title_of(path, meta):
    t = (meta.get('title') or '').strip()
    if t:
        return t
    return notes.strip_ext(os.path.basename(path))


# --------------------------------------------------------------- состояние


NEW, CHANGED, SAME, NO_ID = 'new', 'changed', 'same', 'no-id'


def state_of(path):
    """Что с документом относительно ENOT. -> (состояние, мета).

    new      в ENOT ещё нет (нет yonote_id)
    changed  выгружен, но файл после этого менялся
    same     совпадает с выгруженным
    no-id    есть отметка synced, но нет ни yonote_id, ни коллекции
    """
    meta, _, _ = read_front(path)
    yid = (meta.get('yonote_id') or '').strip()
    coll = (meta.get('collection_id') or '').strip()
    if not yid:
        return (NEW if coll else NO_ID), meta
    old = (meta.get('synced_hash') or '').strip()
    return (SAME if old and old == body_hash(path) else CHANGED), meta


def survey(base_dir, skip=('Backup',)):
    """Обход базы: что расходится с ENOT. -> список записей."""
    out = []
    for dirpath, dirnames, filenames in os.walk(base_dir):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(('_', '.')) and d not in skip]
        for fn in sorted(filenames):
            if not fn.endswith('.md') or fn == 'README.md':
                continue
            full = os.path.join(dirpath, fn)
            st, meta = state_of(full)
            out.append({
                'rel': os.path.relpath(full, base_dir).replace(os.sep, '/'),
                'state': st,
                'title': title_of(full, meta),
                'url_id': (meta.get('url_id') or '').strip(),
                'synced_at': (meta.get('synced_at') or '').strip(),
            })
    return out


# ---------------------------------------------------------------- выгрузка


def push(path, cfg=None, force=False):
    """Выгружает документ в ENOT. -> (получилось, сообщение).

    Существующий документ обновляется по yonote_id, новый создаётся в своей
    коллекции. После записи результат перечитывается: ответ на create/update
    может выглядеть успешным, а текст в ENOT — не тем.
    """
    cfg = cfg if cfg is not None else settings.load()
    st, meta = state_of(path)
    if st == NO_ID:
        return False, 'в файле нет collection_id — непонятно, куда выгружать'
    if st == SAME and not force:
        return True, 'уже совпадает с ENOT'

    _, _, text = read_front(path)
    payload_text = prepare_text(text)
    title = title_of(path, meta)
    yid = (meta.get('yonote_id') or '').strip()

    if yid:
        _, err = call('documents.update',
                      {'id': yid, 'title': title, 'text': payload_text,
                       'publish': True}, cfg)
        if err:
            return False, err
    else:
        payload = {'title': title, 'text': payload_text,
                   'collectionId': (meta.get('collection_id') or '').strip(),
                   'publish': True}
        parent = (meta.get('parent_id') or '').strip()
        if parent:
            payload['parentDocumentId'] = parent
        res, err = call('documents.create', payload, cfg)
        if err:
            return False, err
        yid = ((res or {}).get('data') or {}).get('id') or ''
        if not yid:
            return False, 'ENOT не вернул id созданного документа'

    # проверка: читаем то, что легло в ENOT
    res, err = call('documents.info', {'id': yid}, cfg)
    if err:
        return False, f'записала, но проверить не смогла: {err}'
    data = (res or {}).get('data') or {}
    got = (data.get('text') or '').strip()
    if not got:
        return False, 'в ENOT документ пустой — выгрузка не удалась'

    # ссылки: они должны остаться видимыми
    lost = [u for u in re.findall(r'https?://[^\s<>)\]]+', payload_text)
            if u not in got]
    warn = f'; ссылок не видно в ENOT: {len(lost)}' if lost else ''

    write_front(path, {
        'yonote_id': yid,
        'url_id': data.get('urlId') or meta.get('url_id') or '',
        'status': 'synced',
        'synced_at': date.today().isoformat(),
        'synced_hash': body_hash(path),
    })
    where = f'{len(got)} знаков'
    return True, f'выгружено в ENOT ({where}){warn}'


def doc_url(path, cfg=None):
    cfg = cfg if cfg is not None else settings.load()
    meta, _, _ = read_front(path)
    uid = (meta.get('url_id') or '').strip()
    if not uid:
        return ''
    site = (cfg.get('yonote_url') or 'https://app.yonote.ru').rstrip('/')
    return f'{site}/doc/{uid}'


if __name__ == '__main__':
    import sys

    cfg = settings.load()
    print('ключ доступа:', 'есть' if api_key(cfg) else 'НЕТ')
    if len(sys.argv) > 1 and sys.argv[1] == '--check':
        res, err = call('auth.info', {}, cfg)
        if err:
            print('связь:', err)
            sys.exit(1)
        d = (res or {}).get('data') or {}
        print('связь есть:', (d.get('team') or {}).get('url'),
              '·', (d.get('user') or {}).get('name'))
        sys.exit(0)

    rows = survey(cfg['base'])
    by = {}
    for r in rows:
        by[r['state']] = by.get(r['state'], 0) + 1
    print('всего документов:', len(rows))
    for k in (SAME, CHANGED, NEW, NO_ID):
        if by.get(k):
            print(f'  {k}: {by[k]}')
    for r in rows:
        if r['state'] in (CHANGED, NEW):
            print(f'  {r["state"]:<8} {r["rel"]}')
