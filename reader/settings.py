#!/usr/bin/env python3
"""Настройки читалки: где лежит база документов.

Путь к базе не прошит в коде, а берётся из config.json рядом с этим файлом.
Благодаря этому один и тот же код работает и на виртуальной машине, и на
хостовой: меняется только config.json, который в репозиторий не попадает.

Порядок поиска пути к базе:
  1. параметр --base в командной строке;
  2. переменная окружения ORCHESTRA_BASE;
  3. поле "base" в config.json;
  4. папка Yonote на рабочем столе текущего пользователя.
"""
import fnmatch
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                  # корень Orchestra
CONFIG = os.path.join(ROOT, 'config.json')
ENV = os.path.join(ROOT, '.env')

DEFAULTS = {
    'base': '',
    'port': 8765,
    'host': '127.0.0.1',
    'yonote_url': 'https://anewera.yonote.ru',
    # куда читалка отправляет задание, чтобы Ника взялась за работу сама
    'hermes_webhook': '',
    'hermes_secret': '',
    # Кому разрешено писать замечания, кроме самой машины с читалкой.
    # Читалка живёт в виртуальной машине, а работать удобнее с хоста: там
    # микрофон и голосовой ввод. Список масок вида '192.168.1.*' открывает
    # запись доверенным адресам локальной сети. Пустой список — как раньше,
    # запись только с 127.0.0.1.
    'write_allow': [],
    # С какого адреса выходить к ENOT, минуя туннель VPN.
    #
    # ENOT стоит на российском сервере и отбивает зарубежные адреса: TLS
    # обрывается на рукопожатии. На машине поднят sing-tun, который забирает
    # весь трафик, поэтому запрос уходит через туннель и не доходит.
    #
    # Привязка исходящего сокета к домашнему адресу возвращает запрос в обычную
    # сеть: проверено — 0.2 секунды вместо обрыва. Работает при запуске со
    # стороны Windows, где этот адрес существует.
    #
    #   ''              как раньше, через системный маршрут
    #   'auto'          читалка сама подберёт подходящий адрес и запомнит
    #   '192.168.1.140' точный адаптер
    'direct_bind': 'auto',
}


# ------------------------------------------------------------------ секреты
#
# Ключи и секреты живут в `.env` в корне Orchestra, а не в config.json.
# Причина: config.json хочется однажды показать или скопировать на другую
# машину, а секрет — нет. `.env` — привычное место, его понимают все
# инструменты, и он целиком лежит в .gitignore.
#
# Формат обычный:
#
#     YONOTE_API_KEY=...
#     AGENT_SECRET_NIKA=...
#
# В agents.json при этом пишется не сам секрет, а имя переменной:
# "secret": "env:AGENT_SECRET_NIKA". Так реестр можно спокойно читать и
# показывать, не боясь засветить ключ.

_ENV_LINE = re.compile(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$')


def load_env(path=None):
    """Читает .env в словарь. Переменные окружения важнее файла."""
    out = {}
    p = path or ENV
    try:
        with open(p, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                m = _ENV_LINE.match(line)
                if not m:
                    continue
                val = m.group(2).strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in '"\'':
                    val = val[1:-1]
                out[m.group(1)] = val
    except OSError:
        pass
    return out


def env_get(name, default=''):
    """Значение секрета: сначала окружение, потом .env."""
    v = (os.environ.get(name) or '').strip()
    if v:
        return v
    return (load_env().get(name) or default).strip()


def env_set(name, value, path=None):
    """Пишет секрет в .env, не задевая остальные строки.

    Файл правится построчно, а не перезаписывается из словаря: в нём могут
    лежать комментарии и чужие ключи, и терять их нельзя.
    """
    p = path or ENV
    name = str(name).strip()
    value = str(value or '').strip()
    lines = []
    try:
        with open(p, encoding='utf-8') as f:
            lines = f.read().splitlines()
    except OSError:
        lines = ['# Секреты Orchestra. Этот файл не попадает в репозиторий.']

    done = False
    for i, line in enumerate(lines):
        m = _ENV_LINE.match(line)
        if m and m.group(1) == name:
            lines[i] = f'{name}={value}' if value else f'# {name}='
            done = True
            break
    if not done and value:
        lines.append(f'{name}={value}')

    tmp = p + '.part'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines).rstrip('\n') + '\n')
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return True


def env_name_for(agent_id):
    """Имя переменной под секрет агента: nika -> AGENT_SECRET_NIKA."""
    safe = re.sub(r'[^A-Za-z0-9]+', '_', str(agent_id or '')).strip('_').upper()
    return f'AGENT_SECRET_{safe or "AGENT"}'



def write_allowed(addr, cfg=None):
    """Разрешена ли запись с этого адреса.

    Своя машина разрешена всегда. Остальные — только если попадают под маску
    из `write_allow`. Маски простые (fnmatch), потому что список ведёт человек
    руками, а не система управления доступом: '192.168.1.*' читается сразу,
    в отличие от записи через маску подсети.
    """
    if addr in ('127.0.0.1', '::1'):
        return True
    cfg = cfg if cfg is not None else load()
    for pattern in cfg.get('write_allow') or []:
        if fnmatch.fnmatch(str(addr), str(pattern).strip()):
            return True
    return False


def guess_base():
    """Папка Yonote на рабочем столе — типичное место базы."""
    home = os.path.expanduser('~')
    for desk in ('Desktop', 'Рабочий стол', 'OneDrive/Desktop'):
        p = os.path.join(home, desk.replace('/', os.sep), 'Yonote')
        if os.path.isdir(p):
            return p
    # WSL: рабочий стол Windows виден через /mnt/c
    for user in ('andrn',):
        p = f'/mnt/c/Users/{user}/Desktop/Yonote'
        if os.path.isdir(p):
            return p
    return ''


def translate(path):
    """Windows-путь -> WSL-путь и обратно, чтобы один config.json работал везде.

    Один и тот же config.json читается и из Windows, и из WSL. В нём удобно
    держать привычный путь `C:\\Users\\...`, поэтому под Linux он переводится
    в `/mnt/c/Users/...`, а под Windows — наоборот.
    """
    if not path:
        return path
    p = path.strip()

    if os.name == 'nt':
        m = re.match(r'^/mnt/([a-zA-Z])/(.*)$', p)
        if m:
            return f'{m.group(1).upper()}:\\' + m.group(2).replace('/', '\\')
        return p

    m = re.match(r'^([a-zA-Z]):[\\/](.*)$', p)
    if m:
        return f'/mnt/{m.group(1).lower()}/' + m.group(2).replace('\\', '/')
    return p


def load():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG):
        try:
            with open(CONFIG, encoding='utf-8') as f:
                cfg.update(json.load(f) or {})
        except (OSError, ValueError) as e:
            print(f'config.json не прочитался ({e}), беру значения по умолчанию')

    env = os.environ.get('ORCHESTRA_BASE')
    if env:
        cfg['base'] = env
    cfg['base'] = translate(cfg.get('base') or '')

    # Открытая база из bases.json важнее поля в config.json: баз может быть
    # несколько, и они не пересекаются. Поле `base` остаётся как запись о
    # первой базе — на машине, где bases.json ещё не появился, всё работает
    # по-прежнему. Импорт внутри функции: bases.py читает нас, и наверху это
    # замкнуло бы круг.
    if not env:
        try:
            import bases
            picked = bases.current_path(cfg['base'])
            if picked:
                cfg['base'] = picked
        except Exception:
            pass

    if not cfg['base']:
        cfg['base'] = guess_base()
    if cfg['base']:
        cfg['base'] = os.path.abspath(cfg['base'])
    return cfg


def save(cfg):
    keep = {k: cfg[k] for k in DEFAULTS if k in cfg}
    with open(CONFIG, 'w', encoding='utf-8') as f:
        json.dump(keep, f, ensure_ascii=False, indent=1)


if __name__ == '__main__':
    c = load()
    print('config.json:', CONFIG, '(есть)' if os.path.exists(CONFIG) else '(нет)')
    for k, v in c.items():
        if k == 'hermes_secret' and v:
            v = v[:4] + '…' + v[-4:]
        print(f'  {k}: {v}')
    if not c['base']:
        print('\nБаза не найдена. Укажите путь в config.json полем "base".')
    elif not os.path.isdir(c['base']):
        print(f'\nПапки нет: {c["base"]}')
    else:
        n = sum(1 for _, _, fs in os.walk(c['base']) for f in fs if f.endswith('.md'))
        print(f'\nбаза на месте, документов .md: {n}')
    if c.get('hermes_webhook'):
        print('связь с Никой настроена: кнопка «отправить в работу» запустит её сама')
    else:
        print('связь с Никой не настроена: задание будет ждать в очереди')
