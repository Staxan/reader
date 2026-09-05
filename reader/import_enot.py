#!/usr/bin/env python3
"""Первая загрузка базы: из ENOT или из папки бэкапа.

База наполняется один раз. Дальше она живёт своей жизнью: версии, замечания,
правки. Поэтому загрузка устроена так, что перезаписать ничего не может:

- в непустую папку загрузка не идёт вообще;
- существующий файл не перезаписывается никогда, даже при повторном запуске;
- документы разных баз не пересекаются, потому что база — это отдельная папка,
  а не общий склад (см. `bases.py`).

Второй режим — проверка: что появилось или изменилось в ENOT после загрузки.
Проверка ничего не делает сама, только показывает список. Забирает выбранное
`pull()` — и снова: новые документы создаёт, чужие правки в готовые файлы не
вносит, обновляет только то, что Андрей отметил.

Структура на диске повторяет ENOT:

    <база>/<Коллекция>/<Документ>.md
    <база>/<Коллекция>/<Документ с детьми>/_index.md
    <база>/<Коллекция>/<Документ с детьми>/<Ребёнок>.md

В front-matter каждого файла — отметки ENOT, по ним потом работает выгрузка:

    title, yonote_id, url_id, parent_id, collection_id, kind,
    status: synced, synced_at, synced_hash, revision
"""
import os
import re
import shutil
import urllib.parse
from datetime import date

import notes
import settings
import sync_yonote as sy

PAGE = 100
MAX_PAGES = 40            # 4000 документов в одной коллекции — с запасом

INDEX_NAME = '_index.md'

# Windows запрещает эти знаки в именах файлов, а половина заголовков в ENOT
# содержит двоеточие. Бэкап ENOT прячет их в %XX и читать такие имена
# невозможно, поэтому заменяем на похожие разрешённые знаки.
BAD = {'<': '‹', '>': '›', ':': ' -', '"': '”', '/': '-', '\\': '-',
       '|': '-', '?': '', '*': '·'}
RESERVED = {'CON', 'PRN', 'AUX', 'NUL'} | {f'COM{i}' for i in range(1, 10)} \
    | {f'LPT{i}' for i in range(1, 10)}


def sane_name(title, fallback='без имени'):
    """Заголовок ENOT -> имя файла, читаемое человеком."""
    t = (title or '').strip() or fallback
    t = ''.join(BAD.get(ch, ch) for ch in t)
    t = ''.join(ch for ch in t if ord(ch) >= 32)
    t = re.sub(r'\s+', ' ', t).strip(' .')
    if t.upper() in RESERVED:
        t = t + '_'
    return t[:120].strip() or fallback


def unquote_name(name):
    """Имя из бэкапа ENOT: «Книга%3A план.md» -> «Книга - план.md»."""
    base = name[:-3] if name.endswith('.md') else name
    if '%' in base:
        try:
            base = urllib.parse.unquote(base)
        except Exception:
            pass
    out = sane_name(base)
    return out + '.md' if name.endswith('.md') else out


# Windows отказывается открывать файл, если весь путь длиннее 260 знаков, и
# ошибка выглядит как «файла нет». В книге есть пути по 204 знака
# («Паспорт глав 4 — блочная структура/Блок 4. …»), поэтому длину проверяем
# заранее и укорачиваем имя файла, а не папки: папка одна на многих.
PATH_LIMIT = 250


def fit_path(root, rel):
    """Полный путь, укороченный так, чтобы Windows его открыла."""
    full = os.path.join(root, rel.replace('/', os.sep))
    if len(full) <= PATH_LIMIT:
        return full
    head, tail = os.path.split(full)
    stem, ext = (tail[:-3], '.md') if tail.endswith('.md') else (tail, '')
    room = PATH_LIMIT - len(head) - len(os.sep) - len(ext)
    if room < 24:                     # укорачивать нечего: пусть решает система
        return full
    return os.path.join(head, stem[:room].rstrip(' .-—') + ext)


# --------------------------------------------------------------- чтение ENOT


def _live(docs):
    """Отбрасывает архив, удалённое и шаблоны — но не рвёт дерево.

    Тонкость, найденная на живой витрине: у документа-шаблона бывают живые
    дети (в коллекции «Привет» это «Продажи CRM» с семью строками). Если
    выбросить такого родителя целиком, дети теряют папку и валятся в корень
    коллекции слипшимися именами: «Взаимодействие», «Взаимодействие (2)»…
    Поэтому шаблон остаётся, если на него кто-то ссылается.
    """
    ok = [d for d in docs if not d.get('archivedAt') and not d.get('deletedAt')]
    parents = {d.get('parentDocumentId') for d in ok if not d.get('template')}
    return [d for d in ok if not d.get('template') or d['id'] in parents]


def fetch_collection(cid, cfg, progress=None):
    """Все живые документы коллекции. -> (список, ошибка).

    `documents.list` отдаёт текст целиком, поэтому дозапрашивать каждый
    документ через `documents.info` не нужно: 780 документов — это 9 запросов,
    а не 780.
    """
    out, off = [], 0
    for _ in range(MAX_PAGES):
        res, err = sy.call('documents.list',
                           {'collectionId': cid, 'limit': PAGE, 'offset': off}, cfg)
        if err:
            return None, err
        page = res.get('data') or []
        out += page
        if progress:
            progress(len(out))
        if len(page) < PAGE:
            break
        off += PAGE
    return _live(out), ''


def fetch_all(cfg, progress=None):
    """Вся витрина: коллекции и их документы. -> (коллекции, документы, ошибка).

    Заархивированное и шаблоны отбрасываются: архив всё ещё приходит в списке,
    и без отбора база получила бы дубли уже удалённых глав.
    """
    colls, err = sy.collections(cfg)
    if err:
        return [], [], err
    docs = []
    for i, c in enumerate(colls, 1):
        if progress:
            progress(f'коллекция {i} из {len(colls)}: {c["name"]}', len(docs))
        got, e = fetch_collection(c['id'], cfg)
        if e:
            return colls, docs, f'коллекция «{c["name"]}»: {e}'
        docs += got
    return colls, docs, ''


# ------------------------------------------------------------------- раскладка


def layout(colls, docs):
    """Куда какой документ ляжет. -> список записей {rel, doc, kind}.

    Папкой становится документ, у которого есть дети: его собственный текст
    уезжает в `_index.md` рядом с детьми — так устроена и нынешняя база.
    """
    by_id = {d['id']: d for d in docs}
    kids = {}
    for d in docs:
        p = d.get('parentDocumentId')
        if p and p in by_id:
            kids.setdefault(p, []).append(d)

    cname = {c['id']: sane_name(c['name'], 'Коллекция') for c in colls}
    used = {}                       # папка -> занятые имена

    def free(folder, name, ext='.md'):
        """Свободное имя в папке: два документа с одним заголовком бывают."""
        taken = used.setdefault(folder, set())
        stem = name
        n = 2
        while (stem + ext).lower() in taken:
            stem = f'{name} ({n})'
            n += 1
        taken.add((stem + ext).lower())
        return stem

    out = []

    def place(doc, folder):
        title = doc.get('title') or ''
        children = kids.get(doc['id']) or []
        if children:
            stem = free(folder, sane_name(title, 'Документ'), '')
            sub = f'{folder}/{stem}' if folder else stem
            out.append({'rel': f'{sub}/{INDEX_NAME}', 'doc': doc, 'kind': 'folder'})
            for ch in sorted(children, key=lambda x: (x.get('title') or '').lower()):
                place(ch, sub)
        else:
            stem = free(folder, sane_name(title, 'Документ'))
            rel = f'{folder}/{stem}.md' if folder else f'{stem}.md'
            out.append({'rel': rel, 'doc': doc, 'kind': 'doc'})

    for c in colls:
        folder = cname[c['id']]
        roots = [d for d in docs
                 if d.get('collectionId') == c['id']
                 and not (d.get('parentDocumentId') in by_id)]
        if not roots:
            continue
        for d in sorted(roots, key=lambda x: (x.get('title') or '').lower()):
            place(d, folder)
    return out


# --------------------------------------------------------------------- запись


def front_of(doc, kind='doc'):
    """Блок front-matter для документа ENOT."""
    body = (doc.get('text') or '').strip()
    fields = [
        ('title', doc.get('title') or ''),
        ('yonote_id', doc.get('id') or ''),
        ('url_id', doc.get('urlId') or ''),
        ('parent_id', doc.get('parentDocumentId') or ''),
        ('collection_id', doc.get('collectionId') or ''),
        ('kind', kind),
        ('status', 'synced'),
        ('synced_at', date.today().isoformat()),
        ('synced_hash', notes.text_hash(body)),
        ('revision', doc.get('revision') or 0),
    ]
    lines = '\n'.join(f'{k}: {v}' for k, v in fields)
    return f'---\n{lines}\n---\n'


def write_doc(full, doc, kind='doc', overwrite=False):
    """Пишет документ на диск. -> записан ли.

    Существующий файл не трогается: база загружается один раз, дальше в ней
    живут версии и замечания Андрея. Перезапись стёрла бы работу.
    """
    if os.path.exists(full) and not overwrite:
        return False
    body = (doc.get('text') or '').strip()
    notes.atomic_write(full, front_of(doc, kind) + body + ('\n' if body else ''))
    return True


# ---------------------------------------------------------- первая загрузка


def check_target(path):
    """Можно ли загружать в эту папку. -> ошибка или пустая строка."""
    p = settings.translate(path or '')
    if not p:
        return 'не указана папка базы'
    if os.path.isfile(p):
        return 'по этому пути лежит файл, а не папка'
    if os.path.isdir(p):
        for dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            if any(f.endswith('.md') for f in filenames):
                return ('в папке уже есть документы — база загружается один раз '
                        'и не перезаписывается')
    return ''


def first_load(base, cfg, progress=None):
    """Загрузка базы из ENOT. -> (сколько записано, сообщение об ошибке).

    Ничего не перезаписывает: если в папке уже есть документы, загрузка не
    начинается вовсе.
    """
    path = settings.translate(base['path'])
    err = check_target(path)
    if err:
        return 0, err

    def say(text, n=0):
        if progress:
            progress(text, n)

    say('спрашиваю ENOT…')
    colls, docs, err = fetch_all(cfg, progress=lambda t, n: say(t, n))
    if err and not docs:
        return 0, err

    plan = layout(colls, docs)
    say(f'раскладываю {len(plan)} документов…', 0)

    written = 0
    for i, item in enumerate(plan, 1):
        full = fit_path(path, item['rel'])
        os.makedirs(os.path.dirname(full), exist_ok=True)
        if write_doc(full, item['doc'], item['kind']):
            written += 1
        if progress and (i % 25 == 0 or i == len(plan)):
            say(f'записано {i} из {len(plan)}', i)
    tail = f'; часть коллекций не прочиталась: {err}' if err else ''
    return written, tail.lstrip('; ') if err else ''


def from_backup(base, src, progress=None):
    """Загрузка базы из папки бэкапа ENOT. -> (сколько, ошибка).

    В бэкапе нет отметок ENOT: там просто markdown с именами вида
    «Книга%3A план.md». Имена раскодируются, front-matter не выдумывается —
    у таких документов нет `yonote_id`, и выгрузка сама попросит указать
    коллекцию, когда до этого дойдёт дело.
    """
    src = settings.translate(src or '')
    if not os.path.isdir(src):
        return 0, f'нет папки бэкапа: {src}'
    path = settings.translate(base['path'])
    err = check_target(path)
    if err:
        return 0, err

    files = []
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        for fn in sorted(filenames):
            if fn.endswith('.md'):
                files.append(os.path.join(dirpath, fn))
    if not files:
        return 0, 'в папке бэкапа нет файлов .md'

    written = 0
    for i, f in enumerate(files, 1):
        rel = os.path.relpath(f, src)
        parts = [unquote_name(p) for p in rel.split(os.sep)]
        dst = fit_path(path, '/'.join(parts))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not os.path.exists(dst):
            shutil.copyfile(f, dst)
            written += 1
        if progress and (i % 25 == 0 or i == len(files)):
            progress(f'скопировано {i} из {len(files)}', i)
    return written, ''


# -------------------------------------------------------------- проверка ENOT


def local_index(path):
    """Что уже лежит в базе: yonote_id -> (относительный путь, revision)."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        for fn in filenames:
            if not fn.endswith('.md'):
                continue
            full = os.path.join(dirpath, fn)
            try:
                meta, _, _ = sy.read_front(full)
            except OSError:
                continue
            yid = (meta.get('yonote_id') or '').strip()
            if not yid:
                continue
            try:
                rev = int((meta.get('revision') or '0').strip() or 0)
            except ValueError:
                rev = 0
            out[yid] = (os.path.relpath(full, path).replace(os.sep, '/'), rev)
    return out


def survey_remote(base, cfg, progress=None):
    """Что нового в ENOT после загрузки. -> (записи, ошибка).

    Сравнение идёт по `revision` — счётчику правок на стороне ENOT. Сравнивать
    текстом нельзя: ENOT отдаёт markdown без разметки, и тогда каждый документ
    выглядел бы изменённым.

    Ничего не меняет. Только список.
    """
    path = settings.translate(base['path'])
    if not os.path.isdir(path):
        return [], f'нет папки базы: {path}'

    colls, docs, err = fetch_all(cfg, progress=progress)
    if err and not docs:
        return [], err

    have = local_index(path)
    plan = {item['doc']['id']: item for item in layout(colls, docs)}

    rows = []
    for d in docs:
        yid = d['id']
        item = plan.get(yid)
        rel = item['rel'] if item else ''
        if yid not in have:
            rows.append({'id': yid, 'rel': rel, 'title': d.get('title') or '',
                         'state': 'new', 'revision': d.get('revision') or 0,
                         'chars': len(d.get('text') or ''),
                         'updated': (d.get('updatedAt') or '')[:10]})
            continue
        old_rel, old_rev = have[yid]
        rev = int(d.get('revision') or 0)
        if rev != old_rev:
            rows.append({'id': yid, 'rel': old_rel, 'title': d.get('title') or '',
                         'state': 'changed', 'revision': rev, 'was': old_rev,
                         'chars': len(d.get('text') or ''),
                         'updated': (d.get('updatedAt') or '')[:10]})

    remote_ids = {d['id'] for d in docs}
    for yid, (rel, _) in have.items():
        if yid not in remote_ids:
            rows.append({'id': yid, 'rel': rel, 'title': os.path.basename(rel)[:-3],
                         'state': 'gone', 'revision': 0, 'chars': 0, 'updated': ''})

    rows.sort(key=lambda r: ({'new': 0, 'changed': 1, 'gone': 2}[r['state']], r['rel']))
    return rows, (err or '')


def pull(base, cfg, ids, progress=None):
    """Забирает выбранные документы из ENOT. -> (создано, обновлено, ошибка).

    Новые документы создаются. Изменённые перезаписываются только если Андрей
    отметил их сам — и только те, что он отметил. Ничего не решается за него.
    """
    path = settings.translate(base['path'])
    if not os.path.isdir(path):
        return 0, 0, f'нет папки базы: {path}'
    want = set(ids or [])
    if not want:
        return 0, 0, 'не выбрано ни одного документа'

    colls, docs, err = fetch_all(cfg, progress=progress)
    if err and not docs:
        return 0, 0, err

    have = local_index(path)
    plan = {item['doc']['id']: item for item in layout(colls, docs)}

    made = upd = 0
    for yid in want:
        item = plan.get(yid)
        if not item:
            continue
        if yid in have:
            full = os.path.join(path, have[yid][0].replace('/', os.sep))
            write_doc(full, item['doc'], item['kind'], overwrite=True)
            upd += 1
        else:
            full = fit_path(path, item['rel'])
            os.makedirs(os.path.dirname(full), exist_ok=True)
            if write_doc(full, item['doc'], item['kind']):
                made += 1
    return made, upd, ''
