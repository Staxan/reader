#!/usr/bin/env python3
"""Связь с агентом Hermes через webhook.

Это то, что раньше делал hermes_link.py: POST на адрес подписки с подписью
HMAC-SHA256. Разница одна — адрес и секрет берутся из записи агента, а не из
единственного поля в config.json.
"""
import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 8

# Запрос идёт на локальный адрес, но Windows может держать системный прокси
# (например, от VPN) с пустым списком исключений. Тогда обращение к 127.0.0.1
# уходит в прокси и возвращается 502, не доходя до Hermes. Поэтому ходим
# напрямую, игнорируя настройки прокси.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def sign(secret, body):
    """Подпись HMAC-SHA256, как её ждёт webhook Hermes."""
    mac = hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
    return 'sha256=' + mac


def send(agent, doc_rel, job):
    """Отправляет задание. -> (получилось, сообщение)."""
    url = (agent.get('url') or '').strip()
    who = agent.get('name') or agent.get('id') or 'агент'
    if not url:
        return False, f'у «{who}» не задан адрес — задание ждёт в очереди'

    notes_list = job.get('notes') or []
    payload = {
        'event': 'book_notes_ready',
        'doc': doc_rel,
        'base': job.get('base', ''),
        'version': job.get('version', ''),
        'notes': notes_list,
        'count': len(notes_list),
        'author': job.get('author', 'Андрей'),
        'agent': agent.get('id', ''),
        'agent_name': who,
        'created': job.get('created', ''),
    }
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    from agents import registry
    secret = registry.secret_of(agent)
    if secret:
        headers['X-Hub-Signature-256'] = sign(secret, body)

    req = urllib.request.Request(url, data=body, headers=headers, method='POST')
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as r:
            if 200 <= r.status < 300:
                return True, f'задание ушло: {who}'
            return False, f'{who}: Hermes ответил {r.status}'
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8', 'replace')[:160]
        except Exception:
            pass
        return False, f'{who}: Hermes ответил {e.code}: {detail}'.strip()
    except Exception as e:
        return False, f'{who} недоступен ({type(e).__name__}) — задание ждёт'


def check(agent):
    """Проверка связи: /health на том же порту, что и подписка.

    Отдельная проверка нужна, чтобы страница «Агенты» не создавала настоящих
    заданий. /health не требует подписи и отвечает даже без подписки.
    """
    url = (agent.get('url') or '').strip()
    if not url:
        return False, 'адрес не задан'
    p = urllib.parse.urlsplit(url)
    health = f'{p.scheme}://{p.netloc}/health'
    try:
        with _OPENER.open(urllib.request.Request(health, method='GET'),
                          timeout=4) as r:
            if 200 <= r.status < 300:
                return True, 'связь есть'
            return False, f'ответ {r.status}'
    except urllib.error.HTTPError as e:
        return False, f'ответ {e.code}'
    except Exception as e:
        return False, f'не отвечает ({type(e).__name__})'
