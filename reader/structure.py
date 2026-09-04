#!/usr/bin/env python3
"""Распознаёт структуру текста главы, из которого Yonote вырезал разметку.

Yonote через API отдаёт текст без markdown: заголовки без `#`, выделения без
`**`, пустых строк между абзацами нет. Здесь структура восстанавливается по
признакам самого текста, без правки файлов.

Правила распознавания заголовка:
  - строка короче 85 знаков;
  - не заканчивается на . ! ? : ; , » — то есть это не предложение;
  - следующая строка непустая (у заголовка всегда есть текст под ним);
  - либо начинается с номера «5. Название», либо просто короткая строка.

Опорная мысль: одиночное короткое предложение между длинными абзацами.
Это интерпретация, а не факт текста, поэтому в читалке её можно выключить.

Результат — список блоков. Каждый блок: {'t': тип, ...}
Типы: h1 meta lead p key h2 h3 ul ol quote code srcheader source
"""
import re

FRONT = re.compile(r'^---\r?\n(.*?)\r?\n---\r?\n?', re.S)
SRC_HEADER = re.compile(r'^#{0,4}\s*Источники(\s+к\s+главе\s*\d*)?\s*[:.]?\s*$', re.I)
MD_HEAD = re.compile(r'^(#{1,4})\s+(.+)$')
NUM_HEAD = re.compile(r'^(\d{1,2})\.\s+(.{2,70})$')
LIST_ITEM = re.compile(r'^[-*•—]\s+(.+)$')
NUM_ITEM = re.compile(r'^(\d{1,2})[.)]\s+(.+)$')
META_LINE = re.compile(r'^(Черновик|Версия|Редакция)\s', re.I)
SENT_END = ('.', '!', '?', ':', ';', ',', '»', '…')
# конец заголовка: кавычка допустима («Что значит «кибер»»), точка — нет
HEAD_BAD_END = ('.', '!', '?', ':', ';', ',', '…', '—', '-')
# опорная мысль утверждает, а не анонсирует
ANNOUNCE = re.compile(
    r'^(Чтобы|Дальше|Далее|Здесь|Ниже|Выше|Сначала|Теперь|Итак|Разберём|'
    r'Рассмотрим|Начнём|Это глава|Эта глава|В этой главе|Вот|А |И |Или |Но )', re.I)

HEAD_MAX = 85          # заголовок не бывает длиннее
KEY_MIN, KEY_MAX = 45, 150   # рамки опорной мысли
LEAD_MIN = 60          # лид должен быть содержательным


def strip_front(raw):
    """Убирает служебный блок локальной базы, возвращает (мета, текст)."""
    m = FRONT.match(raw)
    if not m:
        return {}, raw
    meta = {}
    for line in m.group(1).splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            meta[k.strip()] = v.strip()
    return meta, raw[m.end():]


def looks_like_heading(line, nxt):
    """Похожа ли строка на заголовок, у которого Yonote срезал решётки."""
    s = line.strip()
    if not s or len(s) > HEAD_MAX:
        return False
    if s.endswith(HEAD_BAD_END):
        return False
    if not nxt or not nxt.strip():
        return False
    if LIST_ITEM.match(s):
        return False
    # «5. Простое определение» — заголовок; «5. Длинный текст…» — пункт списка
    m = NUM_ITEM.match(s)
    if m:
        inner = m.group(2).rstrip()
        return len(inner) <= 70 and not inner.endswith(HEAD_BAD_END)
    # в заголовке не бывает нескольких точек-предложений
    if s.count('. ') > 1:
        return False
    return True


def parse(raw):
    """Текст главы -> список блоков."""
    _, text = strip_front(raw)
    lines = text.replace('\r\n', '\n').split('\n')
    # схлопываем пустые строки, но помним, что они были
    rows = [l.rstrip() for l in lines]

    blocks = []
    i = 0
    n = len(rows)
    seen_h1 = False
    in_sources = False
    body_paras = 0

    def nxt_nonempty(k):
        while k < n and not rows[k].strip():
            k += 1
        return rows[k] if k < n else ''

    while i < n:
        s = rows[i].strip()
        if not s:
            i += 1
            continue

        # блок кода
        if s.startswith('```'):
            buf = []
            i += 1
            while i < n and not rows[i].strip().startswith('```'):
                buf.append(rows[i])
                i += 1
            i += 1
            blocks.append({'t': 'code', 'text': '\n'.join(buf)})
            continue

        # заголовок с решётками, если разметка сохранилась
        m = MD_HEAD.match(s)
        if m:
            lvl, title = len(m.group(1)), m.group(2).strip()
            if SRC_HEADER.match(s):
                blocks.append({'t': 'srcheader', 'text': title})
                in_sources = True
            elif lvl == 1 and not seen_h1:
                blocks.append({'t': 'h1', 'text': title})
                seen_h1 = True
            else:
                blocks.append({'t': 'h2' if lvl <= 2 else 'h3', 'text': title})
            i += 1
            continue

        # блок источников без решёток
        if SRC_HEADER.match(s):
            blocks.append({'t': 'srcheader', 'text': s.rstrip(':. ')})
            in_sources = True
            i += 1
            continue

        # первая строка документа — название главы
        if not seen_h1:
            blocks.append({'t': 'h1', 'text': s})
            seen_h1 = True
            i += 1
            continue

        # строка «Черновик 1.1. Написано от имени проекта…»
        if META_LINE.match(s) and len(s) < 160:
            blocks.append({'t': 'meta', 'text': s})
            i += 1
            continue

        # внутри блока источников каждый номер — отдельный пункт
        if in_sources:
            m = NUM_ITEM.match(s)
            if m:
                num, parts = m.group(1), [m.group(2)]
                i += 1
                while i < n:
                    t = rows[i].strip()
                    if not t or NUM_ITEM.match(t):
                        break
                    parts.append(t)
                    i += 1
                blocks.append({'t': 'source', 'num': num, 'parts': parts})
                continue
            blocks.append({'t': 'p', 'text': s})
            i += 1
            continue

        # цитата
        if s.startswith('>'):
            buf = []
            while i < n and rows[i].strip().startswith('>'):
                buf.append(rows[i].strip().lstrip('>').strip())
                i += 1
            blocks.append({'t': 'quote', 'text': ' '.join(buf)})
            continue

        # маркированный список
        if LIST_ITEM.match(s):
            items = []
            while i < n:
                mm = LIST_ITEM.match(rows[i].strip())
                if not mm:
                    if not rows[i].strip():
                        i += 1
                        continue
                    break
                items.append(mm.group(1))
                i += 1
            blocks.append({'t': 'ul', 'items': items})
            continue

        # заголовок без решёток
        if looks_like_heading(s, nxt_nonempty(i + 1)):
            m = NUM_ITEM.match(s)
            if m:
                blocks.append({'t': 'h2', 'num': m.group(1), 'text': m.group(2).strip()})
            else:
                blocks.append({'t': 'h2', 'text': s})
            i += 1
            continue

        # нумерованный список: короткая нумерация с длинным текстом
        m = NUM_ITEM.match(s)
        if m and len(m.group(2)) > 70:
            items = []
            while i < n:
                mm = NUM_ITEM.match(rows[i].strip())
                if not mm:
                    if not rows[i].strip():
                        i += 1
                        continue
                    break
                items.append((mm.group(1), mm.group(2)))
                i += 1
            if len(items) > 1:
                blocks.append({'t': 'ol', 'items': items})
                continue
            i -= len(items)
            s = rows[i].strip()

        # абзац: строка = абзац, если пустых строк в тексте нет
        buf = [s]
        i += 1
        while i < n:
            t = rows[i].strip()
            if not t:
                break
            if (looks_like_heading(t, nxt_nonempty(i + 1)) or MD_HEAD.match(t)
                    or LIST_ITEM.match(t) or SRC_HEADER.match(t)
                    or t.startswith(('>', '```'))):
                break
            # соседняя длинная строка — это следующий абзац, не продолжение
            if s.endswith(SENT_END[:3] + ('»',)) or len(t) > 40:
                break
            buf.append(t)
            i += 1
        para = ' '.join(buf)

        prev = blocks[-1]['t'] if blocks else ''
        if prev in ('h1', 'meta') and body_paras == 0 and len(para) >= LEAD_MIN:
            blocks.append({'t': 'lead', 'text': para})
        else:
            blocks.append({'t': 'p', 'text': para})
        body_paras += 1

    mark_key_thoughts(blocks)
    return blocks


def mark_key_thoughts(blocks):
    """Помечает одиночные короткие предложения между длинными абзацами."""
    for idx, b in enumerate(blocks):
        if b['t'] != 'p':
            continue
        text = b['text']
        if not (KEY_MIN <= len(text) <= KEY_MAX):
            continue
        if not text.endswith(('.', '!', '?')):
            continue
        if text.count('. ') > 1:
            continue
        # анонс «дальше будет» опорной мыслью не считается
        if ANNOUNCE.match(text):
            continue
        before = next((blocks[j] for j in range(idx - 1, -1, -1)
                       if blocks[j]['t'] in ('p', 'lead', 'key')), None)
        after = next((blocks[j] for j in range(idx + 1, len(blocks))
                      if blocks[j]['t'] in ('p', 'lead', 'key')), None)
        if not before or not after:
            continue
        # подряд идущие короткие фразы — это ритм автора, а не акцент
        if blocks[idx - 1]['t'] == 'key':
            continue
        if len(before.get('text', '')) < 200 or len(after.get('text', '')) < 200:
            continue
        b['t'] = 'key'


def stats(blocks):
    out = {}
    for b in blocks:
        out[b['t']] = out.get(b['t'], 0) + 1
    return out


def to_markdown(blocks):
    """Обратно в markdown — для восстановления разметки в файлах."""
    out = []
    for b in blocks:
        t = b['t']
        if t == 'h1':
            out += [f'# {b["text"]}', '']
        elif t == 'meta':
            out += [b['text'], '']
        elif t == 'h2':
            num = f'{b["num"]}. ' if b.get('num') else ''
            out += [f'## {num}{b["text"]}', '']
        elif t == 'h3':
            out += [f'### {b["text"]}', '']
        elif t == 'srcheader':
            out += [f'## {b["text"]}', '']
        elif t == 'source':
            out.append(f'{b["num"]}. ' + b['parts'][0])
            out += b['parts'][1:]
            out.append('')
        elif t in ('p', 'lead', 'key'):
            out += [b['text'], '']
        elif t == 'quote':
            out += [f'> {b["text"]}', '']
        elif t == 'ul':
            out += [f'- {x}' for x in b['items']]
            out.append('')
        elif t == 'ol':
            out += [f'{num}. {txt}' for num, txt in b['items']]
            out.append('')
        elif t == 'code':
            out += ['```', b['text'], '```', '']
    return '\n'.join(out).rstrip() + '\n'


if __name__ == '__main__':
    import sys
    for p in sys.argv[1:]:
        blocks = parse(open(p, encoding='utf-8').read())
        print(f'--- {p}')
        print('   ', stats(blocks))
        for b in blocks[:14]:
            label = b['t'].upper()
            txt = b.get('text') or str(b.get('items') or b.get('parts'))
            print(f'    {label:9} {txt[:78]}')
