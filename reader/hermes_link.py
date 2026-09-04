#!/usr/bin/env python3
"""Мост между кнопкой «отправить в работу» и агентом.

Оставлен для совместимости: на него ссылаются `.bat` в корне Orchestra и старые
привычки. Вся работа теперь в пакете `agents/` — там реестр агентов и способы
связи. Здесь только тонкая обёртка.

Проверка связи:

    python reader/hermes_link.py --test
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agents
import settings
from agents import registry


def send(doc_rel, job, cfg=None, agent_id=''):
    """Отправляет задание агенту. -> (получилось, сообщение).

    Агент берётся по id, а без него — тот, что назначен по умолчанию.
    """
    cfg = cfg or settings.load()
    reg = registry.load(cfg)
    agent = registry.get(agent_id, reg) if agent_id else registry.default_agent(reg)
    if not agent:
        return False, 'агентов в настройках нет — задание ждёт в очереди'
    return agents.send(agent, doc_rel, job)


if __name__ == '__main__':
    cfg = settings.load()
    reg = registry.load(cfg)
    print('реестр:', registry.reg_path(),
          '(есть)' if os.path.exists(registry.reg_path()) else '(нет, беру config.json)')
    if not reg['agents']:
        print('агентов нет: ни в agents.json, ни в config.json')
        sys.exit(1)

    for a in reg['agents']:
        mark = '*' if a['id'] == reg['default'] else ' '
        state = 'вкл' if a.get('enabled', True) else 'выкл'
        print(f'{mark} {a["name"]:<10} {state:<4} {a.get("kind", ""):<15} '
              f'{a.get("url") or a.get("command") or "(адрес не задан)"}')

    if len(sys.argv) > 1 and sys.argv[1] == '--test':
        print()
        for a in registry.enabled(reg):
            ok, msg = agents.check(a)
            print(f'  {a["name"]}: {"ok" if ok else "нет"} — {msg}')
