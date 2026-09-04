#!/usr/bin/env python3
"""Версии документов, цитаты Андрея и правки Ники для читалки.

Правила, на которых всё держится:

1. Версии неизменяемы. Как только создана v2, файл v1 только для чтения.
   Поэтому привязка цитаты не может съехать: текст под ней больше не меняется.
2. Замечания живут отдельным файлом в `_notes`. Текст главы остаётся чистым
   и готовым к заливке в ENOT и переносу на сайт.
3. Запись атомарная: сначала временный файл, потом подмена одним движением.
   Если процесс умрёт в середине, старый файл останется целым.
4. История append-only: события только добавляются, никогда не переписываются.
"""
import hashlib
import json
import os
import re
import tempfile
import time
from datetime import datetime

NOTES_DIR = '_notes'
FRONT = re.compile(r'^---\r?\n(.*?)\r?\n---\r?\n?', re.S)

# Основной формат версии: «Глава 1. Лебедь, рак и щука_1.0.md».
# Первая цифра — смысл (переписана подглава, новый блок), вторая — правка
# формулировок. Старые обозначения читаются тоже, чтобы ничего не потерялось.
VER_MAIN = re.compile(r'_(\d+)\.(\d+)\s*$')
VER_OLD = re.compile(
    r'\s*[—–-]\s*(?:v(\d+)(?:\.(\d+))?|черновик\s*(\d+)(?:\.(\d+))?|'
    r'версия\s*(\d+)(?:\.(\d+))?)\s*$', re.I)


# ------------------------------------------------------------------ имена


def strip_ext(filename):
    return filename[:-3] if filename.endswith('.md') else filename


def base_name(filename):
    """«Глава 1 ..._1.2.md» -> «Глава 1 ...». Основа семейства версий."""
    name = strip_ext(filename)
    name = VER_MAIN.sub('', name)
    name = VER_OLD.sub('', name)
    return name.strip()


def version_of(filename):
    """Версия файла как (смысл, правка). Файл без обозначения — 1.0."""
    name = strip_ext(filename)
    m = VER_MAIN.search(name)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = VER_OLD.search(name)
    if m:
        g = [x for x in m.groups() if x is not None]
        major = int(g[0])
        minor = int(g[1]) if len(g) > 1 else 0
        return (major, minor)
    return (1, 0)


def vstr(v):
    """(1, 2) -> «1.2»"""
    return f'{v[0]}.{v[1]}'


def version_file(base, v):
    return f'{base}_{v[0]}.{v[1]}.md'


def bump(v, kind='minor'):
    """Следующая версия: 'minor' — правки, 'major' — смысл."""
    return (v[0] + 1, 0) if kind == 'major' else (v[0], v[1] + 1)


def family(base_dir, filename):
    """Все версии документа в папке: [((смысл, правка), имя файла)] по порядку."""
    base = base_name(filename)
    out = []
    try:
        entries = os.listdir(base_dir)
    except OSError:
        return []
    for fn in entries:
        if not fn.endswith('.md') or fn.startswith('_'):
            continue
        if base_name(fn) == base:
            out.append((version_of(fn), fn))
    out.sort(key=lambda x: x[0])
    return out


def latest(base_dir, filename):
    fam = family(base_dir, filename)
    return fam[-1] if fam else (version_of(filename), filename)


def is_latest(base_dir, filename):
    return latest(base_dir, filename)[1] == filename


# ------------------------------------------------------------------ файлы


def text_hash(s):
    return hashlib.sha256((s or '').strip().encode('utf-8')).hexdigest()[:12]


def atomic_write(path, data):
    """Пишем рядом и подменяем. Файл либо старый целиком, либо новый целиком."""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    d = os.path.dirname(path) or '.'
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.tmp-', suffix='.part')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\r\n') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def notes_path(doc_path):
    d, fn = os.path.split(doc_path)
    return os.path.join(d, NOTES_DIR, fn[:-3] + '.json')


def history_path(doc_path):
    d, fn = os.path.split(doc_path)
    return os.path.join(d, NOTES_DIR, base_name(fn) + '.history.json')


def load_notes(doc_path):
    p = notes_path(doc_path)
    if not os.path.exists(p):
        return {'notes': [], 'edits': []}
    try:
        with open(p, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {'notes': [], 'edits': []}
    data.setdefault('notes', [])
    data.setdefault('edits', [])
    return data


def save_notes(doc_path, data):
    data['doc'] = os.path.basename(doc_path)
    data['version'] = vstr(version_of(os.path.basename(doc_path)))
    data['updated'] = datetime.now().isoformat(timespec='seconds')
    atomic_write(notes_path(doc_path),
                 json.dumps(data, ensure_ascii=False, indent=1))


def load_history(doc_path):
    p = history_path(doc_path)
    if not os.path.exists(p):
        return {'base': base_name(os.path.basename(doc_path)), 'events': []}
    try:
        with open(p, encoding='utf-8') as f:
            h = json.load(f)
    except (OSError, ValueError):
        return {'base': base_name(os.path.basename(doc_path)), 'events': []}
    h.setdefault('events', [])
    return h


def log_event(doc_path, kind, **fields):
    """Паспорт документа: что, когда, зачем. Только добавление."""
    h = load_history(doc_path)
    ev = {'at': datetime.now().isoformat(timespec='seconds'),
          'version': vstr(version_of(os.path.basename(doc_path))),
          'kind': kind}
    ev.update(fields)
    h['events'].append(ev)
    atomic_write(history_path(doc_path),
                 json.dumps(h, ensure_ascii=False, indent=1))
    return ev


# ------------------------------------------------------------------ цитаты


def new_id(prefix, existing):
    n = 1
    used = {x.get('id') for x in existing}
    while f'{prefix}{n}' in used:
        n += 1
    return f'{prefix}{n}'


def add_note(doc_path, anchor, comment, author='Андрей'):
    """Цитата Андрея: привязка к куску текста плюс его замечание."""
    data = load_notes(doc_path)
    note = {
        'id': new_id('n', data['notes']),
        'kind': 'quote',
        'author': author,
        'created': datetime.now().isoformat(timespec='seconds'),
        'anchor': anchor,
        'comment': comment.strip(),
        'status': 'open',
    }
    data['notes'].append(note)
    save_notes(doc_path, data)
    log_event(doc_path, 'note_added', note_id=note['id'],
              quote=anchor.get('text', '')[:160], comment=note['comment'][:400])
    return note


def update_note(doc_path, note_id, comment=None, status=None):
    data = load_notes(doc_path)
    for n in data['notes']:
        if n['id'] != note_id:
            continue
        if comment is not None:
            n['comment'] = comment.strip()
        if status is not None:
            n['status'] = status
        n['edited'] = datetime.now().isoformat(timespec='seconds')
        save_notes(doc_path, data)
        log_event(doc_path, 'note_updated', note_id=note_id,
                  status=n.get('status'))
        return n
    return None


def delete_note(doc_path, note_id):
    data = load_notes(doc_path)
    before = len(data['notes'])
    data['notes'] = [n for n in data['notes'] if n['id'] != note_id]
    if len(data['notes']) == before:
        return False
    save_notes(doc_path, data)
    log_event(doc_path, 'note_deleted', note_id=note_id)
    return True


def edit_author(doc_path, fallback='Ника'):
    """Кто правит документ: агент из привязки, а не имя, вшитое в код.

    Когда агент был один, подпись «Ника» была верна всегда. При нескольких
    агентах правки должны подписываться исполнителем — иначе через месяц из
    истории не понять, кто менял версию 1.1. Привязку можно сменить в любой
    момент, поэтому имя фиксируется в момент правки.
    """
    try:
        import binding
        import settings
        import agents.registry as registry

        base = settings.load()['base']
        aid, _, _ = binding.resolve(doc_path, base)
        if not aid:
            job = None
            try:
                import jobs
                job = jobs.load(doc_path)
            except Exception:
                pass
            if job and job.get('agent_name'):
                return job['agent_name']
            a = registry.default_agent()
            return a['name'] if a else fallback
        return registry.name_of(aid) or fallback
    except Exception:
        # читалка должна работать и без реестра — старое поведение
        return fallback


def add_edit(doc_path, anchor, from_note=None, note_text='', was='',
             why='', author=''):
    """Правка в новой версии: помечает изменённый фрагмент.

    Если правка сделана по замечанию из прежней версии, это замечание
    получает статус «сделано» и перестаёт наследоваться в новую версию.

    Автор не указан — берётся из привязки документа к агенту.
    """
    data = load_notes(doc_path)
    edit = {
        'id': new_id('e', data['edits']),
        'kind': 'edit',
        'author': author or edit_author(doc_path),
        'created': datetime.now().isoformat(timespec='seconds'),
        'anchor': anchor,
        'from_note': from_note,
        'note_text': note_text,
        'was': was,
        'why': why,
    }
    data['edits'].append(edit)
    save_notes(doc_path, data)
    log_event(doc_path, 'edit_made', edit_id=edit['id'], from_note=from_note,
              why=why[:300], author=edit['author'])
    if from_note:
        close_origin(doc_path, from_note, 'done')
    return edit


def find_origin(doc_path, note_id):
    """Ищет замечание по id в этой и прежних версиях. -> (файл, замечание)."""
    d, fn = os.path.split(doc_path)
    cur = version_of(fn)
    pure = note_id.split('-')[-1] if note_id else ''
    for v, name in sorted(family(d, fn), key=lambda x: x[0], reverse=True):
        if v > cur:
            continue
        p = os.path.join(d, name)
        for n in load_notes(p)['notes']:
            if n['id'] == pure:
                return p, n
    return None, None


def close_origin(doc_path, note_id, status='done'):
    """Ставит статус замечанию там, где оно лежит."""
    p, n = find_origin(doc_path, note_id)
    if not p or not n:
        return False
    data = load_notes(p)
    for x in data['notes']:
        if x['id'] == n['id']:
            x['status'] = status
            x['closed'] = datetime.now().isoformat(timespec='seconds')
    save_notes(p, data)
    log_event(p, 'note_updated', note_id=n['id'], status=status)
    return True


# ------------------------------------------------------------------ привязка


def relocate(blocks, anchor):
    """Ищет фрагмент замечания в тексте. -> (номер блока, 'ok'|'moved'|'lost').

    Сначала смотрим там, где фрагмент был. Если абзац изменился или сдвинулся,
    ищем по всему тексту: замечание не должно теряться из-за смены нумерации.
    """
    frag = (anchor.get('text') or '').strip()
    if not frag:
        return None, 'lost'
    bi = anchor.get('block')
    if isinstance(bi, int) and 0 <= bi < len(blocks):
        t = blocks[bi].get('text') or ''
        if frag in t:
            same = anchor.get('bhash') and anchor['bhash'] == text_hash(t)
            return bi, ('ok' if same else 'moved')
    for i, b in enumerate(blocks):
        if frag in (b.get('text') or ''):
            return i, 'moved'
    return None, 'lost'


def check_anchor(blocks, anchor):
    """Совместимость: только состояние привязки, без номера блока."""
    return relocate(blocks, anchor)[1]


def inherited_notes(doc_path):
    """Открытые замечания из прежних версий, ещё не отработанные здесь.

    Замечания не копируются в новую версию: они остаются в своей и
    показываются в новой приглушённо, пока по ним не сделана правка.
    """
    d, fn = os.path.split(doc_path)
    cur = version_of(fn)
    handled = {e.get('from_note') for e in load_notes(doc_path)['edits']
               if e.get('from_note')}
    handled |= {h.split('-')[-1] for h in handled if h}

    out = []
    for v, name in family(d, fn):
        if v >= cur:
            continue
        data = load_notes(os.path.join(d, name))
        for n in data['notes']:
            if n.get('status') != 'open' or n['id'] in handled:
                continue
            item = dict(n)
            item['from_version'] = vstr(v)
            item['origin_id'] = n['id']
            item['origin_file'] = name
            item['id'] = f'v{v[0]}_{v[1]}-{n["id"]}'
            out.append(item)
    return out


def annotations(doc_path, blocks):
    """Собирает подсветки и врезки для отрисовки страницы.

    marks: {номер блока: [{text, occ, cls, id}]}
    boxes: {номер блока: [(вид, элемент, состояние)]} — вставляются после блока

    Три вида: 'quote' — замечание Андрея в этой версии, 'inherit' —
    открытое замечание из прежней версии, 'edit' — правка Ники.
    """
    data = load_notes(doc_path)
    marks, boxes, orphans = {}, {}, []

    def place(kind, item, cls):
        bi, state = relocate(blocks, item['anchor'])
        if bi is None:
            orphans.append((kind, item, 'lost'))
            return
        marks.setdefault(bi, []).append({
            'text': item['anchor']['text'], 'occ': item['anchor'].get('occ', 0),
            'cls': cls, 'id': item['id']})
        boxes.setdefault(bi, []).append((kind, item, state))

    for n in data['notes']:
        if n.get('status') in ('done', 'rejected', 'wontfix'):
            continue                       # отработанное не засоряет текст
        place('quote', n, 'q-note')

    for n in inherited_notes(doc_path):
        place('inherit', n, 'q-old')

    for e in data['edits']:
        place('edit', e, 'q-edit')

    return {'marks': marks, 'boxes': boxes, 'orphans': orphans,
            'notes': data['notes'], 'edits': data['edits'],
            'inherited': inherited_notes(doc_path)}


# ------------------------------------------------------------------ версии


def create_version(doc_path, author='Андрей', kind='minor'):
    """Копия документа как следующая версия. Прежняя становится только чтением.

    kind='minor' — правки формулировок: 1.0 -> 1.1
    kind='major' — изменился смысл, подглава, структура: 1.3 -> 2.0
    """
    d, fn = os.path.split(doc_path)
    base = base_name(fn)
    fam = family(d, fn)
    cur = fam[-1][0] if fam else version_of(fn)
    nxt = bump(cur, kind)
    new_fn = version_file(base, nxt)
    new_path = os.path.join(d, new_fn)
    if os.path.exists(new_path):
        return None, f'версия {vstr(nxt)} уже есть'

    with open(doc_path, encoding='utf-8') as f:
        raw = f.read()

    m = FRONT.match(raw)
    if m:
        meta_lines, body = m.group(1).splitlines(), raw[m.end():]
        out = []
        seen = set()
        for line in meta_lines:
            if ':' not in line:
                out.append(line)
                continue
            k, v = line.split(':', 1)
            k = k.strip()
            seen.add(k)
            if k == 'title':
                out.append(f'title: {base}_{vstr(nxt)}')
            elif k in ('yonote_id', 'url_id'):
                out.append(f'{k}: ')          # новая версия ещё не в ENOT
            elif k == 'status':
                out.append('status: draft')
            elif k in ('synced_hash', 'synced_at'):
                out.append(f'{k}: ')
            elif k == 'version':
                out.append(f'version: {vstr(nxt)}')
            elif k == 'from_version':
                out.append(f'from_version: {vstr(cur)}')
            elif k == 'created':
                out.append('created: ' + datetime.now().isoformat(timespec='seconds'))
            else:
                out.append(line)
        for extra, val in (('version', vstr(nxt)),
                           ('from_version', vstr(cur)),
                           ('created', datetime.now().isoformat(timespec='seconds'))):
            if extra not in seen:
                out.append(f'{extra}: {val}')
        raw = '---\n' + '\n'.join(out) + '\n---\n' + body.lstrip('\n')

    atomic_write(new_path, raw)
    log_event(new_path, 'version_created', author=author,
              from_version=vstr(cur), file=new_fn, bump=kind)
    return new_path, None


def freeze_state(base_dir, filename):
    """Можно ли писать в этот файл: писать разрешено только в последнюю версию."""
    v, last = latest(base_dir, filename)
    if last == filename:
        return True, v
    return False, v
