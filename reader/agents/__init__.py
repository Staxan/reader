#!/usr/bin/env python3
"""Отправка задания агенту. Выбор способа связи по полю kind.

Читалка не знает, как устроен конкретный агент. Она вызывает одну функцию:

    send(agent, doc_rel, job) -> (получилось, сообщение)

Каждый способ связи живёт в своём файле:

    hermes.py   webhook Hermes с подписью HMAC (работает)
    cli.py      запуск агента командой в терминале (заготовка)

Новый тип агента — новый файл рядом и строка в ADAPTERS. Код читалки при
этом не меняется.
"""
from agents import cli as _cli
from agents import hermes as _hermes
from agents import registry

ADAPTERS = {
    'hermes-webhook': _hermes,
    'cli': _cli,
}


def send(agent, doc_rel, job):
    """Отправляет задание агенту. -> (получилось, сообщение)."""
    if not agent:
        return False, 'агент не выбран — задание ждёт в очереди'
    if not agent.get('enabled', True):
        return False, f'{agent.get("name") or agent["id"]} отключён в настройках'

    kind = agent.get('kind') or 'hermes-webhook'
    mod = ADAPTERS.get(kind)
    if not mod:
        return False, f'неизвестный способ связи: {kind}'
    return mod.send(agent, doc_rel, job)


def check(agent):
    """Проверка связи без создания задания. -> (готов, сообщение)."""
    if not agent:
        return False, 'агент не выбран'
    mod = ADAPTERS.get(agent.get('kind') or 'hermes-webhook')
    if not mod:
        return False, f'неизвестный способ связи: {agent.get("kind")}'
    return mod.check(agent)


__all__ = ['send', 'check', 'registry', 'ADAPTERS']
