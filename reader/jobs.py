#!/usr/bin/env python3
"""Задание Нике: очередь работы и ход выполнения для читалки.

Когда Андрей нажимает «Отправить в работу», читалка создаёт файл задания
рядом с документом. Ника его читает, выполняет и пишет обратно ход работы:
шаг за шагом, с отметками времени. Читалка перечитывает файл и обновляет
полосу прогресса и ленту шагов.

Файл задания живёт в `_notes/<документ>.job.json` и переживает перезапуск:
если браузер закрыть, состояние не потеряется.

Состояния задания:
    queued   — создано, Ника ещё не взялась
    running  — в работе
    done     — закончено
    failed   — сорвалось, в поле error причина
    stopped  — прервано Андреем

Состояния шага: wait, run, done, fail, skip
"""
import json
import os
from datetime import datetime

from notes import (NOTES_DIR, atomic_write, base_name, inherited_notes,
                   load_notes, strip_ext, version_of, vstr)

STALE_SEC = 120          # столько тишины считаем зависанием


def job_path(doc_path):
    d, fn = os.path.split(doc_path)
    return os.path.join(d, NOTES_DIR, strip_ext(fn) + '.job.json')


def now():
    return datetime.now().isoformat(timespec='seconds')


def load(doc_path):
    p = job_path(doc_path)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save(doc_path, job):
    job['updated'] = now()
    atomic_write(job_path(doc_path), json.dumps(job, ensure_ascii=False, indent=1))
    return job


def pending_notes(doc_path):
    """Замечания, которые нужно отработать: свои открытые плюс унаследованные."""
    own = [n for n in load_notes(doc_path)['notes']
           if n.get('status') == 'open']
    return own + inherited_notes(doc_path)


def create(doc_path, author='Андрей', agent_id='', agent_name=''):
    """Создаёт задание из открытых замечаний документа.

    agent_id / agent_name — кто отвечает за документ. Берутся из привязки, а не
    из выбора в момент отправки: агента назначают заранее, на документ или на
    коллекцию. В задании они нужны, чтобы правки в истории подписались
    исполнителем, а не тем, кто написан в коде.
    """
    cur = load(doc_path)
    if cur and cur.get('state') in ('queued', 'running'):
        return None, 'по этому документу уже идёт работа'

    items = pending_notes(doc_path)
    if not items:
        return None, 'нет открытых замечаний'

    d, fn = os.path.split(doc_path)
    steps = [{'title': f'читаю замечания ({len(items)})', 'state': 'wait',
              'at': ''},
             {'title': 'создаю новую версию', 'state': 'wait', 'at': ''}]
    for n in items:
        frag = (n['anchor'].get('text') or '')[:52]
        steps.append({'title': f'правка по «{frag}»', 'state': 'wait',
                      'at': '', 'note': n['id']})
    steps.append({'title': 'проверяю и закрываю замечания', 'state': 'wait',
                  'at': ''})

    short = base_name(fn)
    job = {
        'doc': fn,
        'base': short,
        'title': f'Работаю: {short}',
        'version': vstr(version_of(fn)),
        'author': author,
        'agent': agent_id,
        'agent_name': agent_name,
        'created': now(),
        'state': 'queued',
        'notes': [n['id'] for n in items],
        'steps': steps,
        'result': '',
        'error': '',
        'new_doc': '',
    }
    save(doc_path, job)
    return job, None


def start(doc_path):
    job = load(doc_path)
    if not job:
        return None
    job['state'] = 'running'
    job['started'] = now()
    return save(doc_path, job)


def step(doc_path, index, state='done', title=None):
    """Отмечает шаг. Следующий за ним ставится в работу."""
    job = load(doc_path)
    if not job:
        return None
    steps = job['steps']
    if not (0 <= index < len(steps)):
        return job
    if title:
        steps[index]['title'] = title
    steps[index]['state'] = state
    steps[index]['at'] = now()
    if state == 'done' and index + 1 < len(steps):
        if steps[index + 1]['state'] == 'wait':
            steps[index + 1]['state'] = 'run'
    return save(doc_path, job)


def add_step(doc_path, title, state='run', before_last=False):
    """Добавляет шаг, которого не было в плане. -> номер шага.

    before_last=True вставляет его перед завершающей проверкой, чтобы
    порядок в окне у Андрея оставался логичным.
    """
    job = load(doc_path)
    if not job:
        return None
    item = {'title': title, 'state': state, 'at': now()}
    steps = job['steps']
    if before_last and len(steps) > 1:
        pos = len(steps) - 1
        steps.insert(pos, item)
    else:
        steps.append(item)
        pos = len(steps) - 1
    save(doc_path, job)
    return pos


def finish(doc_path, result='', new_doc='', state='done', error=''):
    job = load(doc_path)
    if not job:
        return None
    for s in job['steps']:
        if s['state'] in ('wait', 'run'):
            s['state'] = 'done' if state == 'done' else 'skip'
    job['state'] = state
    job['result'] = result
    job['error'] = error
    job['new_doc'] = new_doc
    if new_doc:
        job['version_new'] = vstr(version_of(new_doc))
    job['finished'] = now()
    return save(doc_path, job)


def stop(doc_path, reason='прервано'):
    return finish(doc_path, result=reason, state='stopped')


def clear(doc_path):
    p = job_path(doc_path)
    if os.path.exists(p):
        os.remove(p)
        return True
    return False


def progress(job):
    """Сколько шагов закрыто и сколько всего."""
    steps = job.get('steps') or []
    done = sum(1 for s in steps if s['state'] in ('done', 'skip', 'fail'))
    return done, len(steps)


def is_stale(job):
    """Тишина дольше STALE_SEC при незакрытом задании — похоже на зависание.

    Считается и для `queued`: если задание создано, а Ника за две минуты его
    не взяла (шлюз лежал, задание отклонили, набор инструментов был урезан),
    оно так и останется висеть. Без этого одно неудачное задание навсегда
    блокирует кнопку «отправить в работу».
    """
    if not job or job.get('state') not in ('queued', 'running'):
        return False
    try:
        last = datetime.fromisoformat(job.get('updated') or job['created'])
    except (ValueError, KeyError):
        return False
    return (datetime.now() - last).total_seconds() > STALE_SEC


def find_jobs(base_dir):
    """Все активные задания в базе: [(путь к документу, задание)]."""
    out = []
    for dirpath, dirnames, filenames in os.walk(base_dir):
        if os.path.basename(dirpath) != NOTES_DIR:
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            continue
        for fn in filenames:
            if not fn.endswith('.job.json'):
                continue
            doc = os.path.join(os.path.dirname(dirpath),
                               fn[:-len('.job.json')] + '.md')
            job = load(doc)
            if job and job.get('state') in ('queued', 'running'):
                out.append((doc, job))
    return out
