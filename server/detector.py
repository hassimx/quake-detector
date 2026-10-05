"""Потоковый детектор: принимает записи кусками и сообщает о новых тревогах.

Сам детектор НЕ изменён: фильтр, окна STA/LTA и пороги берутся из step11.py
(функция analyze и константы ON_THR/OFF_THR/GROUP_SEC). Мы только каждый раз
прогоняем её по "скользящему буферу" последних BUFFER_SEC секунд и берём
тревоги, которых ещё не видели. Фильтр и STA/LTA причинные (смотрят только
в прошлое), поэтому новые куски не меняют уже посчитанное.

Ограничения:
  * первые WARMUP_SEC секунд потока тревоги не выдаём (окно LTA и фильтр
    ещё "разгоняются"); в пакетных step11-13 такого пропуска не было;
  * дыры в потоке заполняем нулями, как в step7 (st.merge(fill_value=0)).
"""

import numpy as np
from obspy import Trace
from obspy.signal.trigger import trigger_onset

from step11 import GROUP_SEC, OFF_THR, ON_THR, analyze

BUFFER_SEC = 300   # сколько последних секунд держим в буфере
WARMUP_SEC = 60    # столько первых секунд буфера не доверяем
PEAK_SEC = 10      # пик STA/LTA ищем в первые 10 секунд после начала тревоги


class StreamDetector:
    """Детектор одной станции."""

    def __init__(self):
        self.last_alarm = None  # время последней принятой тревоги (UTCDateTime)
        self._reset()

    def _reset(self):
        self.samples = np.empty(0)
        self.start = None  # время первого отсчёта в буфере
        self.fs = None

    @property
    def end(self):
        """Время сразу после последнего отсчёта буфера."""
        return self.start + len(self.samples) / self.fs

    def push(self, starttime, fs, samples):
        """Добавляет кусок записи; возвращает список новых тревог.

        Тревога: {"time": UTCDateTime, "peak": максимум STA/LTA}.
        """
        samples = np.asarray(samples, dtype=np.float64)
        if self.fs is not None and fs != self.fs:
            self._reset()  # частота дискретизации сменилась: начинаем заново
        if self.start is None:
            self.start, self.fs = starttime, fs
            self.samples = samples
        else:
            gap = round((starttime - self.end) * fs)
            if gap > BUFFER_SEC * fs:
                self._reset()  # слишком большая дыра: буфер начинаем заново
                self.start, self.fs = starttime, fs
                self.samples = samples
            else:
                if gap > 0:
                    samples = np.concatenate([np.zeros(gap), samples])
                elif gap < 0:
                    samples = samples[-gap:]  # кусок пришёл с перекрытием
                if len(samples) == 0:
                    return []
                self.samples = np.concatenate([self.samples, samples])
        excess = len(self.samples) - int(BUFFER_SEC * self.fs)
        if excess > 0:
            self.samples = self.samples[excess:]
            self.start = self.start + excess / self.fs
        return self._detect()

    def _detect(self):
        if len(self.samples) / self.fs <= WARMUP_SEC:
            return []
        tr = Trace(data=self.samples.copy(),
                   header=dict(sampling_rate=self.fs, starttime=self.start))
        _, ratio, _ = analyze(tr)
        new = []
        # Берём "сырые" начала тревог и склеиваем их сами (а не берём готовый
        # список из analyze): склейка должна идти относительно последней
        # принятой тревоги потока, а не относительно начала буфера.
        for on, _off in trigger_onset(ratio, ON_THR, OFF_THR):
            t = self.start + on / self.fs
            if t < self.start + WARMUP_SEC:
                continue
            if self.last_alarm is None or t - self.last_alarm > GROUP_SEC:
                peak = float(ratio[on:on + int(PEAK_SEC * self.fs)].max())
                self.last_alarm = t
                new.append(dict(time=t, peak=peak))
        return new
