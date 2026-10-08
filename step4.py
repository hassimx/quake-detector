"""step 4: try different STA/LTA settings and see which are better"""

from obspy import read, UTCDateTime
from obspy.signal.trigger import classic_sta_lta, trigger_onset

st = read("tohoku_2011.mseed")
st.merge(fill_value=0)
tr = st[0]
tr.detrend("demean")
tr.filter("bandpass", freqmin=1.0, freqmax=8.0)
fs = tr.stats.sampling_rate

# start of the earthquake (origin time). Alarms before it count as false
ORIGIN = UTCDateTime("2011-03-11T05:46:24")
COOLDOWN = 600  # after an alarm we stay silent for 10 minutes (seconds)


def run(on_thr, off_thr, sta_sec, lta_sec):
    ratio = classic_sta_lta(tr.data, int(sta_sec * fs), int(lta_sec * fs))
    triggers = trigger_onset(ratio, on_thr, off_thr)

    # remove repeated alarms: keep only those that come
    # no earlier than COOLDOWN seconds after the previous one
    alarms = []
    for on, off in triggers:
        t = tr.stats.starttime + on / fs
        if not alarms or t - alarms[-1] > COOLDOWN:
            alarms.append(t)

    false_alarms = [t for t in alarms if t < ORIGIN]
    real = [t for t in alarms if t >= ORIGIN]
    delay = round(real[0] - ORIGIN) if real else None
    return len(false_alarms), delay


print("порог  STA  LTA | ложных тревог | первая тревога через (сек после очага)")
for on_thr in (3.5, 5, 7, 10):
    for sta_sec, lta_sec in ((1, 30), (2, 60), (3, 60)):
        off_thr = on_thr / 2
        false_n, delay = run(on_thr, off_thr, sta_sec, lta_sec)
        print(f"{on_thr:>5}  {sta_sec:>3}  {lta_sec:>3} | {false_n:>13} | {delay}")