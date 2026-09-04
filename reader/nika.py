#!/usr/bin/env python3
"""Сторона Ники: читать замечания Андрея, помечать правки, создавать версии.

    nika.py todo                    все открытые замечания по всей базе
    nika.py queue                   активные задания из читалки
    nika.py show <файл>             замечания к документу с контекстом
    nika.py newver <файл>           создать следующую версию (1.0 -> 1.1)
    nika.py newver <файл> --major   смысловая версия (1.3 -> 2.0)
    nika.py mark <файл> ...         пометить правку (см. ниже)
    nika.py status <файл> <n1> done|rejected|wontfix
    nika.py hist <файл>             история документа
    nika.py diff <файл1> <файл2>    что изменилось между версиями

Задание из читалки (окно прогресса у Андрея обновляется само):
    nika.py job show <файл>
    nika.py job start <файл>
    nika.py job step <файл> --index 0 --title "прочитала 3 замечания"
    nika.py job add <файл> --title "нашла ещё одну неточность"
    nika.py job finish <файл> --new "Глава 1 ..._1.2.md"
    nika.py job clear <файл>

Пометить правку:
    nika.py mark "<файл новой версии>" --note n1 --frag "новый текст" \
        --was "что было" --why "почему так"

Пути можно давать относительно базы или полностью.
"""
import argparse
import difflib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jobs
import notes
import settings
import structure

BASE = settings.load()['base']


def resolve(p):
    """Путь к документу: абсолютный, относительно базы или по имени файла.

    Поиск по имени идёт сначала по рабочей части базы, и только потом по
    Backup — иначе «Глава 3 …» находится в бэкапе, а не в рабочей папке.
    """
    if os.path.isabs(p) and os.path.exists(p):
        return p
    cand = os.path.join(BASE, p)
    if os.path.exists(cand):
        return cand

    want = os.path.basename(p).lower()
    exact, partial = [], []
    for dirpath, dirnames, filenames in os.walk(BASE):
        dirnames[:] = [d for d in dirnames if not d.startswith(('_', '.'))]
        for fn in filenames:
            if not fn.endswith('.md'):
                continue
            low = fn.lower()
            full = os.path.join(dirpath, fn)
            if low == want:
                exact.append(full)
            elif want in low:
                partial.append(full)

    def rank(path):
        return (0 if 'Backup' not in path.split(os.sep) else 1, len(path))

    for group in (exact, partial):
        if group:
            group.sort(key=rank)
            return group[0]
    return None


def rel(p):
    return os.path.relpath(p, BASE).replace(os.sep, '/')


def blocks_of(path):
    return structure.parse(open(path, encoding='utf-8').read())


def cmd_todo(_):
    """Все открытые замечания по базе, только в последних версиях."""
    found = 0
    for dirpath, dirnames, filenames in os.walk(BASE):
        dirnames[:] = [d for d in dirnames if not d.startswith(('_', '.'))]
        for fn in sorted(filenames):
            if not fn.endswith('.md') or fn.startswith('_') or fn == 'README.md':
                continue
            full = os.path.join(dirpath, fn)
            is_last, last_v = notes.freeze_state(dirpath, fn)
            if not is_last:
                continue                    # смотрим только актуальные версии

            own = [n for n in notes.load_notes(full)['notes']
                   if n.get('status') == 'open']
            inherited = notes.inherited_notes(full)
            if not own and not inherited:
                continue

            print(f'\n=== {rel(full)}   v{notes.vstr(notes.version_of(fn))}')
            for n in own:
                print(f'  [{n["id"]}] «{n["anchor"]["text"][:76]}»')
                print(f'        {n["comment"]}')
                found += 1
            for n in inherited:
                print(f'  [{n["id"]}] из v{n["from_version"]}  '
                      f'«{n["anchor"]["text"][:64]}»')
                print(f'        {n["comment"]}')
                found += 1
    print(f'\nвсего открытых замечаний: {found}' if found
          else 'открытых замечаний нет')


def cmd_show(a):
    p = resolve(a.file)
    if not p:
        print('не нашла файл:', a.file)
        return 1
    blocks = blocks_of(p)
    data = notes.load_notes(p)
    inherited = notes.inherited_notes(p)
    print(f'{rel(p)}   v{notes.vstr(notes.version_of(os.path.basename(p)))}')
    fam = notes.family(*os.path.split(p))
    print('версии:', ', '.join(notes.vstr(v) for v, _ in fam))
    if not data['notes'] and not data['edits'] and not inherited:
        print('\nзамечаний нет')
        return 0

    def show_note(n, prefix=''):
        bi, state = notes.relocate(blocks, n['anchor'])
        ctx = (blocks[bi].get('text') or '') if bi is not None else ''
        print(f'\n[{n["id"]}] {prefix}{n.get("status", "open")}   '
              f'блок: {bi if bi is not None else "не найден"} ({state})')
        print(f'  цитата:  «{n["anchor"]["text"]}»')
        print(f'  сказал:  {n["comment"]}')
        if ctx:
            print(f'  абзац:   {ctx[:400]}')

    for n in data['notes']:
        show_note(n)
    for n in inherited:
        show_note(n, prefix=f'из v{n["from_version"]}, ')
    for e in data['edits']:
        print(f'\n[{e["id"]}] правка' +
              (f' по {e["from_note"]}' if e.get('from_note') else ''))
        print(f'  стало:   «{e["anchor"]["text"][:120]}»')
        if e.get('was'):
            print(f'  было:    {e["was"][:200]}')
        if e.get('why'):
            print(f'  почему:  {e["why"]}')
    return 0


def cmd_newver(a):
    p = resolve(a.file)
    if not p:
        print('не нашла файл:', a.file)
        return 1
    kind = 'major' if a.major else 'minor'
    who = a.author or notes.edit_author(p)
    new, err = notes.create_version(p, author=who, kind=kind)
    if err:
        print('не вышло:', err)
        return 1
    print('создана:', rel(new))
    print('прежняя версия теперь только для чтения')
    return 0


def cmd_job(a):
    """Работа с заданием: показать, взять, отметить шаг, закрыть."""
    p = resolve(a.file)
    if not p:
        print('не нашла файл:', a.file)
        return 1

    if a.action == 'show':
        job = jobs.load(p)
        if not job:
            print('задания нет')
            return 0
        done, total = jobs.progress(job)
        print(f'{job.get("title", "")}   {job["state"]}   {done}/{total}')
        for i, s in enumerate(job['steps']):
            mark = {'done': '+', 'run': '>', 'wait': '.', 'fail': 'x',
                    'skip': '-'}.get(s['state'], '?')
            print(f'  {i:2} {mark} {s["title"]}')
        if job.get('error'):
            print('ошибка:', job['error'])
        return 0

    if a.action == 'start':
        job = jobs.start(p)
        print('взяла в работу' if job else 'задания нет')
        return 0 if job else 1

    if a.action == 'step':
        if a.index is None:
            print('нужен номер шага: --index N')
            return 1
        job = jobs.step(p, a.index, a.state or 'done', a.title)
        print('отмечено' if job else 'задания нет')
        return 0 if job else 1

    if a.action == 'add':
        if not a.title:
            print('нужен текст шага: --title "..."')
            return 1
        i = jobs.add_step(p, a.title, before_last=not a.at_end)
        print(f'добавлен шаг {i}' if i is not None else 'задания нет')
        return 0 if i is not None else 1

    if a.action == 'finish':
        job = jobs.finish(p, result=a.title or 'готово',
                          new_doc=a.new or '', state=a.state or 'done',
                          error=a.error or '')
        print('закрыто' if job else 'задания нет')
        return 0 if job else 1

    if a.action == 'clear':
        print('убрано' if jobs.clear(p) else 'задания нет')
        return 0

    print('неизвестное действие:', a.action)
    return 1


def cmd_queue(_):
    """Все активные задания в базе."""
    found = jobs.find_jobs(BASE)
    if not found:
        print('активных заданий нет')
        return 0
    for doc, job in found:
        done, total = jobs.progress(job)
        stale = '  (нет ответа больше двух минут)' if jobs.is_stale(job) else ''
        print(f'{rel(doc)}')
        print(f'   {job["state"]}   {done}/{total}   '
              f'замечаний: {len(job.get("notes") or [])}{stale}')
    return 0


def cmd_mark(a):
    """Помечает изменённый фрагмент в новой версии."""
    p = resolve(a.file)
    if not p:
        print('не нашла файл:', a.file)
        return 1
    blocks = blocks_of(p)
    frag = a.frag.strip()
    hit = None
    for i, b in enumerate(blocks):
        t = b.get('text') or ''
        if frag and frag in t:
            hit = (i, t)
            break
    if not hit:
        print('фрагмент не найден в тексте:', frag[:80])
        return 1
    bi, btext = hit

    note_text = ''
    if a.note:
        # замечание может лежать в этой или в любой прежней версии
        src = a.prev and resolve(a.prev)
        if src:
            for n in notes.load_notes(src)['notes']:
                if n['id'] == a.note.split('-')[-1]:
                    note_text = n['comment']
                    break
        if not note_text:
            _, origin = notes.find_origin(p, a.note)
            if origin:
                note_text = origin['comment']
        if not note_text:
            print(f'предупреждение: замечание {a.note} не найдено, '
                  f'помечу правку без его текста')

    anchor = {'block': bi, 'text': frag, 'occ': 0,
              'bhash': notes.text_hash(btext)}
    e = notes.add_edit(p, anchor, from_note=a.note, note_text=note_text,
                       was=a.was or '', why=a.why or '',
                       author=a.author or '')
    print(f'помечено [{e["id"]}] в блоке {bi}, автор: {e["author"]}')
    return 0


def cmd_status(a):
    p = resolve(a.file)
    if not p:
        print('не нашла файл:', a.file)
        return 1
    n = notes.update_note(p, a.note, status=a.status)
    if not n:
        # замечание может лежать в прежней версии
        if notes.close_origin(p, a.note, a.status):
            print(f'[{a.note}] -> {a.status} (в прежней версии)')
            return 0
        print('замечание не найдено')
        return 1
    print(f'[{a.note}] -> {a.status}')
    return 0


def cmd_hist(a):
    p = resolve(a.file)
    if not p:
        print('не нашла файл:', a.file)
        return 1
    h = notes.load_history(p)
    print(f'история: {h.get("base", "")}')
    for ev in h.get('events') or []:
        line = f'  {ev.get("at", "")}  v{ev.get("version", "?")}  {ev.get("kind")}'
        det = [str(ev[k]) for k in ('quote', 'comment', 'why', 'from_note',
                                    'status', 'file') if ev.get(k)]
        print(line + ('   ' + ' · '.join(det) if det else ''))
    return 0


def cmd_diff(a):
    p1, p2 = resolve(a.file1), resolve(a.file2)
    if not p1 or not p2:
        print('не нашла файлы')
        return 1
    t1 = [b.get('text') or '' for b in blocks_of(p1)]
    t2 = [b.get('text') or '' for b in blocks_of(p2)]
    sm = difflib.SequenceMatcher(None, t1, t2)
    changed = 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == 'equal':
            continue
        changed += 1
        print(f'\n--- {op}: блоки {i1}-{i2} -> {j1}-{j2}')
        for x in t1[i1:i2]:
            print('  - ' + x[:220])
        for x in t2[j1:j2]:
            print('  + ' + x[:220])
    print(f'\nизменённых участков: {changed}' if changed else 'различий нет')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')

    sub.add_parser('todo').set_defaults(fn=cmd_todo)

    s = sub.add_parser('show')
    s.add_argument('file')
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser('newver')
    s.add_argument('file')
    s.add_argument('--major', action='store_true',
                   help='смысловая версия: 1.3 -> 2.0 (по умолчанию 1.3 -> 1.4)')
    s.add_argument('--author',
                   help='кто создаёт версию; без него — агент из привязки')
    s.set_defaults(fn=cmd_newver)

    s = sub.add_parser('job', help='задание: show|start|step|add|finish|clear')
    s.add_argument('action', choices=['show', 'start', 'step', 'add',
                                      'finish', 'clear'])
    s.add_argument('file')
    s.add_argument('--index', type=int, help='номер шага для step')
    s.add_argument('--state', help='done|run|fail|skip для step; done|failed|stopped для finish')
    s.add_argument('--title', help='текст шага или итог')
    s.add_argument('--new', help='имя файла новой версии для finish')
    s.add_argument('--error', help='причина срыва для finish')
    s.add_argument('--at-end', action='store_true',
                   help='для add: поставить шаг в самый конец, а не перед проверкой')
    s.set_defaults(fn=cmd_job)

    sub.add_parser('queue', help='активные задания').set_defaults(fn=cmd_queue)

    s = sub.add_parser('mark')
    s.add_argument('file')
    s.add_argument('--frag', required=True, help='изменённый фрагмент в новой версии')
    s.add_argument('--note', help='id замечания, по которому правка')
    s.add_argument('--prev', help='файл предыдущей версии, где лежит замечание')
    s.add_argument('--was', help='как было до правки')
    s.add_argument('--why', help='почему сделано так')
    s.add_argument('--author',
                   help='кто правит; без него — агент из привязки документа')
    s.set_defaults(fn=cmd_mark)

    s = sub.add_parser('status')
    s.add_argument('file')
    s.add_argument('note')
    s.add_argument('status', choices=['open', 'done', 'rejected', 'wontfix'])
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser('hist')
    s.add_argument('file')
    s.set_defaults(fn=cmd_hist)

    s = sub.add_parser('diff')
    s.add_argument('file1')
    s.add_argument('file2')
    s.set_defaults(fn=cmd_diff)

    a = ap.parse_args()
    if not getattr(a, 'fn', None):
        ap.print_help()
        return 2
    return a.fn(a) or 0


if __name__ == '__main__':
    sys.exit(main())
