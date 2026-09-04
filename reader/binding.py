#!/usr/bin/env python3
"""Кто отвечает за документ: привязка агента к документу и к коллекции.

Раньше выбор агента был не нужен: он был один. Когда агентов несколько, каждый
раз спрашивать «кому отправить» — лишний шаг. Поэтому агент привязывается
заранее, а кнопка «отправить в работу» просто смотрит привязку.

Где лежит привязка:

    _notes/<Глава N ...>.agent.json          вся семья версий
    _notes/<Глава N ..._1.2>.agent.json      только эта версия
    _notes/_collection.agent.json            вся папка и всё внутри

Файл семьи важнее файла коллекции, файл версии важнее файла семьи. Привязка на
уровне семьи наследуется новыми версиями сама — заново указывать агента не нужно.

Поиск идёт снизу вверх до первого попадания:

    версия -> семья версий -> папка документа -> папки выше -> база -> default
"""
import json
import os
from datetime import datetime

import notes

FILE_SUFFIX = '.agent.json'
COLL_NAME = '_collection' + FILE_SUFFIX


def _read(path):
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f) or {}
        aid = str(data.get('agent') or '').strip()
        return data if aid else None
    except (OSError, ValueError):
        return None


def _write(path, agent_id, by=''):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    notes.atomic_write(path, json.dumps({
        'agent': str(agent_id or ''),
        'set_by': by or 'Андрей',
        'at': datetime.now().isoformat(timespec='seconds'),
    }, ensure_ascii=False, indent=1))


def _drop(path):
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


# ------------------------------------------------------------ пути привязок


def family_file(doc_path):
    """Привязка всей семьи версий документа."""
    d, fn = os.path.split(doc_path)
    return os.path.join(d, notes.NOTES_DIR, notes.base_name(fn) + FILE_SUFFIX)


def version_file(doc_path):
    """Привязка одной конкретной версии."""
    d, fn = os.path.split(doc_path)
    return os.path.join(d, notes.NOTES_DIR, notes.strip_ext(fn) + FILE_SUFFIX)


def collection_file(folder):
    """Привязка папки-коллекции."""
    return os.path.join(folder, notes.NOTES_DIR, COLL_NAME)


# ------------------------------------------------------------- чтение


def resolve(doc_path, base_dir):
    """Кто отвечает за документ. -> (id агента, откуда взят, где лежит).

    Откуда: 'version', 'family', 'collection', '' (не найдено).
    """
    for path, src in ((version_file(doc_path), 'version'),
                      (family_file(doc_path), 'family')):
        data = _read(path)
        if data:
            return data['agent'], src, path

    folder = os.path.dirname(os.path.abspath(doc_path))
    base_dir = os.path.abspath(base_dir)
    while True:
        data = _read(collection_file(folder))
        if data:
            return data['agent'], 'collection', collection_file(folder)
        if folder == base_dir or len(folder) <= len(base_dir):
            break
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    return '', '', ''


def of_collection(folder):
    """Привязка конкретной папки, без наследования вверх."""
    data = _read(collection_file(folder))
    return data['agent'] if data else ''


def of_document(doc_path):
    """Своя привязка документа: версии или семьи, без наследования от папок."""
    for path in (version_file(doc_path), family_file(doc_path)):
        data = _read(path)
        if data:
            return data['agent']
    return ''


# ------------------------------------------------------------- запись


def bind_document(doc_path, agent_id, by='Андрей', whole_family=True):
    """Привязывает агента к документу.

    whole_family=True — привязка наследуется всеми версиями. Так по умолчанию:
    агента выбирают для главы, а не для файла.
    """
    path = family_file(doc_path) if whole_family else version_file(doc_path)
    _write(path, agent_id, by)
    return path


def unbind_document(doc_path):
    """Снимает свою привязку документа — остаётся наследование от коллекции."""
    dropped = False
    for path in (version_file(doc_path), family_file(doc_path)):
        dropped = _drop(path) or dropped
    return dropped


def bind_collection(folder, agent_id, by='Андрей'):
    path = collection_file(folder)
    _write(path, agent_id, by)
    return path


def unbind_collection(folder):
    return _drop(collection_file(folder))


# ------------------------------------------------------------- для интерфейса


def label(doc_path, base_dir, registry_data=None):
    """Подпись для дерева и шапки: имя агента и откуда он взялся.

    -> {'id', 'name', 'source', 'inherited'}
    """
    import agents.registry as registry

    reg = registry_data if registry_data is not None else registry.load()
    aid, src, _ = resolve(doc_path, base_dir)
    inherited = src == 'collection'
    if not aid:
        d = registry.default_agent(reg)
        return {'id': d['id'] if d else '', 'name': d['name'] if d else '',
                'source': 'default', 'inherited': True}
    return {'id': aid, 'name': registry.name_of(aid, reg),
            'source': src, 'inherited': inherited}
