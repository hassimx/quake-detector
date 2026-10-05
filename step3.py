"""Шаг 3: находим начало землетрясения методом STA/LTA."""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from obspy import read
from obspy.signal.trigger import classic_sta_lta, trigger_onset

# 1. Читаем сохранённую запись (интернет не нужен).
st = read("tohoku_2011.mseed")
st.merge(fill_value=0)  # если запись пришла кусками, склеиваем
tr = st[0]

# 2. Чистим сигнал: убираем постоянную составляющую и оставляем
#    частоты 1-8 Гц, где лучше всего видны сейсмические волны.
tr.detrend("demean")
tr.filter("bandpass", freqmin=1.0, freqmax=8.0)

fs = tr.stats.sampling_rate  # отсчётов в секунду

# 3. Считаем STA/LTA: окна задаём в отсчётах (секунды * частота).
sta_sec, lta_sec = 1, 30
ratio = classic_sta_lta(tr.data, int(sta_sec * fs), int(lta_sec * fs))

# 4. Порог: включаем тревогу, когда ratio > 3.5, выключаем, когда < 1.5.
on_threshold, off_threshold = 3.5, 1.5
triggers = trigger_onset(ratio, on_threshold, off_threshold)

# 5. Печатаем, когда сработала тревога.
if len(triggers) == 0:
    print("Тревога не сработала")
else:
    for on, off in triggers:
        t_on = tr.stats.starttime + on / fs
        print(f"Тревога! Начало: {t_on}")

# 6. Рисуем два графика: сам сигнал и STA/LTA с порогом.
times = tr.times()  # секунды от начала записи
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 6), sharex=True)

ax1.plot(times, tr.data, linewidth=0.5)
ax1.set_ylabel("Сигнал")

ax2.plot(times, ratio, linewidth=0.8)
ax2.axhline(on_threshold, linestyle="--", label="порог тревоги")
ax2.set_ylabel("STA/LTA")
ax2.set_xlabel("Секунды от начала записи")
ax2.legend()

for on, off in triggers:
    ax1.axvline(on / fs, color="red")
    ax2.axvline(on / fs, color="red")

fig.savefig("step3.png", dpi=120)
print("График сохранён в step3.png")