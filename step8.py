"""step 8: how the threshold affects misses and false alarms"""

import statistics

from obspy import read, UTCDateTime
from obspy.signal.trigger import classic_sta_lta, trigger_onset

STA_SEC, LTA_SEC = 1, 30
DETECT_WINDOW = 120  # "detected" if there is an alarm within 2 minutes after the origin
GROUP_SEC = 60       # alarms closer than 60 s count as one

ORIGINS = [
    "2011-03-11T05:46:24", "2021-10-07T13:41:24", "2016-12-28T12:38:49",
    "2022-05-22T15:17:31", "2021-05-13T23:58:14", "2020-06-24T19:47:45",
    "2018-07-07T11:23:50", "2016-04-01T02:39:08", "2023-05-14T08:21:41",
    "2021-08-03T20:33:34",
]
QUIET_FILES = ["quiet_2019-08-15", "quiet_2019-09-20", "quiet_2018-03-12"]


def load(name):
    """read the record, clean it, compute STA/LTA right away"""
    st = read(f"{name}.mseed")
    st.merge(fill_value=0)
    tr = st[0]
    tr.detrend("demean")
    tr.filter("bandpass", freqmin=1.0, freqmax=8.0)
    fs = tr.stats.sampling_rate
    ratio = classic_sta_lta(tr.data, int(STA_SEC * fs), int(LTA_SEC * fs))
    return tr, ratio


def alarms(tr, ratio, on_thr):
    """alarm times at a given threshold (close ones merged)"""
    fs = tr.stats.sampling_rate
    out = []
    for on, off in trigger_onset(ratio, on_thr, on_thr / 2):
        t = tr.stats.starttime + on / fs
        if not out or t - out[-1] > GROUP_SEC:
            out.append(t)
    return out


# load everything once
events = []
for o in ORIGINS:
    tr, ratio = load("ev_" + o[:10])
    events.append((UTCDateTime(o), tr, ratio))
quiet = [load(q) for q in QUIET_FILES]

# hours without earthquakes: 3 quiet hours + 15 minutes before each event
quiet_hours = 3 + len(ORIGINS) * 0.25

print("порог | замечено | ложных в час | медианная задержка")
for on_thr in (5, 7, 10, 15, 20, 30):
    detected, delays, false_n = 0, [], 0
    for origin, tr, ratio in events:
        a = alarms(tr, ratio, on_thr)
        false_n += len([t for t in a if t < origin])
        hits = [t for t in a if origin <= t <= origin + DETECT_WINDOW]
        if hits:
            detected += 1
            delays.append(round(hits[0] - origin))
    for tr, ratio in quiet:
        false_n += len(alarms(tr, ratio, on_thr))
    med = statistics.median(delays) if delays else "-"
    print(f"{on_thr:>5} | {detected:>2} из 10 | "
          f"{false_n / quiet_hours:>12.1f} | {med}")