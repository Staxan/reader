#!/usr/bin/env python3
"""Долгие дела в фоне: загрузка базы и проверка ENOT.

Загрузка 780 документов из ENOT — это десяток запросов по сети и пара минут.
Держать на этом HTTP-соединение нельзя: браузер отвалится по таймауту, а Андрей
увидит зависшую страницу. Поэтому дело уходит в поток, а страница спрашивает
состояние отдельным запросом и рисует полосу.

Одна задача одного вида за раз. Второй запуск, пока первый не кончился, не
ставится в очередь, а честно отклоняется: две загрузки в одну папку — способ
получить мусор.
"""
import threading
import time
import traceback


class Task:
    """Одно долгое дело с понятным состоянием.

    Состояния: `idle` (ещё не запускалось), `running`, `done`, `error`.
    Результат и текст ошибки остаются после завершения — страница успевает их
    прочитать, даже если её открыли уже после конца работы.
    """

    def __init__(self, name):
        self.name = name
        self._lock = threading.Lock()
        self.state = 'idle'
        self.text = ''
        self.n = 0
        self.total = 0
        self.result = None
        self.error = ''
        self.started = 0.0
        self.finished = 0.0
        self.tag = ''          # к какой базе относится дело

    # -------------------------------------------------------------- прогресс

    def say(self, text, n=0, total=0):
        with self._lock:
            self.text = str(text or '')
            if n:
                self.n = int(n)
            if total:
                self.total = int(total)

    def snapshot(self):
        with self._lock:
            out = {'state': self.state, 'text': self.text, 'n': self.n,
                   'total': self.total, 'error': self.error, 'tag': self.tag}
            if self.state == 'running' and self.started:
                out['seconds'] = round(time.time() - self.started)
            elif self.finished and self.started:
                out['seconds'] = round(self.finished - self.started)
            return out

    def busy(self):
        with self._lock:
            return self.state == 'running'

    # ---------------------------------------------------------------- запуск

    def start(self, fn, tag=''):
        """Запускает дело. -> (пошло ли, сообщение).

        `fn` получает саму задачу и может звать `say()`. Что вернёт — ляжет
        в `result`. Исключение превращается в состояние `error`, а не в тихую
        пропажу: молча упавшая загрузка хуже видимой ошибки.
        """
        with self._lock:
            if self.state == 'running':
                return False, f'{self.name}: предыдущее дело ещё не закончилось'
            self.state = 'running'
            self.text = 'начинаю…'
            self.n = self.total = 0
            self.result = None
            self.error = ''
            self.tag = str(tag or '')
            self.started = time.time()
            self.finished = 0.0

        def run():
            try:
                res = fn(self)
            except Exception as e:
                with self._lock:
                    self.state = 'error'
                    self.error = f'{type(e).__name__}: {e}'
                    self.finished = time.time()
                traceback.print_exc()
                return
            with self._lock:
                self.result = res
                self.state = 'done'
                self.finished = time.time()

        threading.Thread(target=run, daemon=True).start()
        return True, 'начала'
