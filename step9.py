"""step 9: what alarms remain even at a high threshold"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from obspy import read
from obspy.signal.trigger import classic_sta_lta, trigger_onset

STA_SEC, LTA_SEC, ON_THR = 1, 30, 20
FILES = ["quiet_2019-08-15", "quiet_2019-09-20", "quiet_2018-03-12"]

for name in FILES:
    st = read(f"{name}.mseed")
    st.merge(fill_value=0)
    tr = st[0]
    tr.detrend("demean")
    tr.filter("bandpass", freqmin=1.0, freqmax=8.0)
    fs = tr.stats.sampling_rate
    ratio = classic_sta_lta(tr.data, int(STA_SEC * fs), int(LTA_SEC * fs))
    trig = trigger_onset(ratio, ON_THR, ON_THR / 2)

    print(f"\n{name}: тревог {len(trig)}")
    for i, (on, off) in enumerate(trig):
        t = tr.stats.starttime + on / fs
        peak = ratio[on:off + 1].max()
        print(f"  {t}  пик STA/LTA = {peak:.0f}")

        # draw 30 s before the alarm and 90 s after
        a = max(0, int(on - 30 * fs))
        b = min(len(tr.data), int(on + 90 * fs))
        plt.figure(figsize=(10, 3))
        plt.plot(tr.times()[a:b], tr.data[a:b], linewidth=0.5)
        plt.axvline(on / fs, color="red")
        plt.title(f"{name} {t}")
        plt.savefig(f"{name}_alarm{i}.png", dpi=100)
        plt.close()