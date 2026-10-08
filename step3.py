"""step 3: find the start of the earthquake with the STA/LTA method"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from obspy import read
from obspy.signal.trigger import classic_sta_lta, trigger_onset

# 1. read the saved record (no internet needed)
st = read("tohoku_2011.mseed")
st.merge(fill_value=0)  # if the record came in pieces, join them
tr = st[0]

# 2. clean the signal: remove the constant component and keep
#    the 1-8 Hz frequencies, where seismic waves are seen best
tr.detrend("demean")
tr.filter("bandpass", freqmin=1.0, freqmax=8.0)

fs = tr.stats.sampling_rate  # samples per second

# 3. compute STA/LTA: the windows are set in samples (seconds * rate)
sta_sec, lta_sec = 1, 30
ratio = classic_sta_lta(tr.data, int(sta_sec * fs), int(lta_sec * fs))

# 4. threshold: switch the alarm on when ratio > 3.5, off when < 1.5
on_threshold, off_threshold = 3.5, 1.5
triggers = trigger_onset(ratio, on_threshold, off_threshold)

# 5. print when the alarm fired
if len(triggers) == 0:
    print("Тревога не сработала")
else:
    for on, off in triggers:
        t_on = tr.stats.starttime + on / fs
        print(f"Тревога! Начало: {t_on}")

# 6. draw two plots: the signal itself and STA/LTA with the threshold
times = tr.times()  # seconds from the start of the record
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