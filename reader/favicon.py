#!/usr/bin/env python3
"""Значок читалки: лупа над страницей текста.

Зачем свой модуль. В браузере вкладка читалки была безымянным тёмным кружком —
среди десятка вкладок её не найти. Картинку взять негде: зависимостей у проекта
нет, рисовать в редакторе и хранить двоичный файл в репозитории не хочется.

Поэтому значок рисуется кодом. SVG — для браузеров, которые его понимают
(это все нынешние), PNG и ICO — для остальных и для ярлыка на рабочем столе.
Растр собирается вручную из простых фигур: круг, отрезок, скруглённый
прямоугольник. Сглаживание — усреднением по девяти точкам на пиксель.

Читается так: лупа над строками текста. Работа читалки — рассматривать текст
близко.
"""
import struct
import zlib

# цвета читалки: фиолетовый акцент, тёплое золото на ручке
BG1 = (0x6f, 0x6d, 0xff)
BG2 = (0x3b, 0x82, 0xf6)
GLASS = (0xf4, 0xf6, 0xff)
LINE = (0x4a, 0x49, 0xc8)
RING = (0xff, 0xff, 0xff)
GRIP = (0xe0, 0xb3, 0x57)

SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#6f6dff"/><stop offset="1" stop-color="#3b82f6"/>
</linearGradient></defs>
<rect width="64" height="64" rx="14" fill="url(#g)"/>
<circle cx="27" cy="27" r="15.5" fill="#f4f6ff"/>
<g stroke="#4a49c8" stroke-width="2.6" stroke-linecap="round">
<path d="M19 22h16"/><path d="M19 28h16"/><path d="M19 34h10"/></g>
<circle cx="27" cy="27" r="15.5" fill="none" stroke="#fff" stroke-width="5"/>
<path d="M38.5 38.5 52 52" stroke="#e0b357" stroke-width="7.5"
 stroke-linecap="round"/>
</svg>"""


# --------------------------------------------------------------- геометрия


def _rounded(x, y, r):
    """Точка внутри скруглённого квадрата 0..1 с радиусом r."""
    cx = min(max(x, r), 1 - r)
    cy = min(max(y, r), 1 - r)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= r * r


def _disc(x, y, cx, cy, r):
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _ring(x, y, cx, cy, r, w):
    d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
    return abs(d - r) <= w / 2


def _bar(x, y, x1, y1, x2, y2, w):
    """Отрезок с круглыми концами — им рисуются строки текста и ручка."""
    vx, vy = x2 - x1, y2 - y1
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((x - x1) * vx + (y - y1) * vy) / L2))
    px, py = x1 + t * vx, y1 + t * vy
    return (x - px) ** 2 + (y - py) ** 2 <= (w / 2) ** 2


# лупа: центр линзы, радиус, толщина обода, ручка
LX, LY, LR = 0.42, 0.42, 0.245
RW = 0.078
H1, H2, HW = (0.60, 0.60), (0.82, 0.82), 0.115
TEXT = (((0.30, 0.345), (0.55, 0.345)),
        ((0.30, 0.425), (0.55, 0.425)),
        ((0.30, 0.505), (0.46, 0.505)))
TW = 0.042


def _sample(x, y):
    """Цвет точки как (r, g, b, a). Слои кладутся снизу вверх."""
    if not _rounded(x, y, 0.22):
        return (0, 0, 0, 0)
    k = (x + y) / 2                                  # наклонный переход цвета
    col = tuple(round(BG1[i] + (BG2[i] - BG1[i]) * k) for i in range(3))

    if _disc(x, y, LX, LY, LR):
        col = GLASS
        for (ax, ay), (bx, by) in TEXT:
            if _bar(x, y, ax, ay, bx, by, TW):
                col = LINE
                break
    if _bar(x, y, H1[0], H1[1], H2[0], H2[1], HW):
        col = GRIP
    if _ring(x, y, LX, LY, LR, RW):
        col = RING
    return (col[0], col[1], col[2], 255)


def raster(size):
    """RGBA-полотно размера size на size, сглаженное по девяти точкам."""
    out = bytearray(size * size * 4)
    step = 1.0 / (size * 3)
    for py in range(size):
        for px in range(size):
            r = g = b = a = 0
            for sy in range(3):
                for sx in range(3):
                    x = (px * 3 + sx + 0.5) * step
                    y = (py * 3 + sy + 0.5) * step
                    cr, cg, cb, ca = _sample(x, y)
                    r += cr * ca
                    g += cg * ca
                    b += cb * ca
                    a += ca
            i = (py * size + px) * 4
            if a:
                out[i] = min(255, round(r / a))
                out[i + 1] = min(255, round(g / a))
                out[i + 2] = min(255, round(b / a))
                out[i + 3] = round(a / 9)
    return bytes(out)


# ------------------------------------------------------------------ форматы


def _chunk(tag, data):
    return (struct.pack('>I', len(data)) + tag + data
            + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))


def png(size=32):
    px = raster(size)
    rows = b''.join(b'\x00' + px[y * size * 4:(y + 1) * size * 4]
                    for y in range(size))
    return (b'\x89PNG\r\n\x1a\n'
            + _chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0))
            + _chunk(b'IDAT', zlib.compress(rows, 9))
            + _chunk(b'IEND', b''))


def ico(sizes=(16, 32, 48)):
    """ICO с картинками PNG внутри — так умеют все нынешние браузеры."""
    blobs = [(s, png(s)) for s in sizes]
    head = struct.pack('<HHH', 0, 1, len(blobs))
    off = 6 + 16 * len(blobs)
    entries, data = b'', b''
    for s, blob in blobs:
        entries += struct.pack('<BBBBHHII', s if s < 256 else 0,
                               s if s < 256 else 0, 0, 0, 1, 32, len(blob), off)
        off += len(blob)
        data += blob
    return head + entries + data


LINKS = ('<link rel="icon" href="/favicon.svg" type="image/svg+xml">'
         '<link rel="alternate icon" href="/favicon.ico" sizes="48x48 32x32 16x16">'
         '<link rel="apple-touch-icon" href="/favicon-180.png">')

if __name__ == '__main__':
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    for name, blob in (('favicon.svg', SVG.encode('utf-8')),
                       ('favicon.ico', ico()),
                       ('favicon-180.png', png(180))):
        with open(os.path.join(here, name), 'wb') as f:
            f.write(blob)
        print(name, len(blob), 'байт')
