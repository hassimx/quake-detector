"""step 7: check on 10 earthquakes and quiet hours"""

import os
import statistics

from obspy import read, UTCDateTime
from obspy.clients.fdsn import Client
from obspy.clients.fdsn.header import FDSNNoDataException
from obspy.signal.trigger import classic_sta_lta, trigger_onset

ON_THR, OFF_THR = 7, 3.5
STA_SEC, LTA_SEC = 1, 30
DETECT_WINDOW = 120  # "detected" if there is an alarm within 2 minutes after the origin
GROUP_SEC = 60       # alarms closer than 60 s count as one

# origin time (UTC) from the USGS catalog
ORIGINS = [
    "2011-03-11T05:46:24",
    "2021-10-07T13:41:24",
    "2016-12-28T12:38:49",
    "2022-05-22T15:17:31",
    "2021-05-13T23:58:14",
    "2020-06-24T19:47:45",
    "2018-07-07T11:23:50",
    "2016-04-01T02:39:08",
    "2023-05-14T08:21:41",
    "2021-08-03T20:33:34",
]

# hours we want to check as "quiet" (the program checks them itself)
QUIET_CANDIDATES = [
    "2019-08-15T04:00:00",
    "2019-09-20T10:00:00",
    "2018-03-12T02:00:00",
]

waves = Client("EARTHSCOPE")
catalog = Client("USGS")


def get_trace(name, start):
    """take the record from a file, and if there is none, download it. No data -> None"""
    path = f"{name}.mseed"
    try:
        if not os.path.exists(path):
            try:
                st = waves.get_waveforms("IU", "MAJO", "00", "BHZ",
                                         start, start + 3600)
            except Exception:
                st = waves.get_waveforms("IU", "MAJO", "*", "BHZ",
                                         start, start + 3600)
            st.write(path, format="MSEED")
        st = read(path)
        st.merge(fill_value=0)
        return st[0]
    except Exception as err:
        print(f"  не удалось получить {name}: {err}")
        return None


def detect(tr):
    """list of alarm times (close alarms are merged into one)"""
    tr.detrend("demean")
    tr.filter("bandpass", freqmin=1.0, freqmax=8.0)
    fs = tr.stats.sampling_rate
    ratio = classic_sta_lta(tr.data, int(STA_SEC * fs), int(LTA_SEC * fs))
    alarms = []
    for on, off in trigger_onset(ratio, ON_THR, OFF_THR):
        t = tr.stats.starttime + on / fs
        if not alarms or t - alarms[-1] > GROUP_SEC:
            alarms.append(t)
    return alarms


def is_quiet(t0):
    """True if there are no M4+ earthquakes nearby (by the USGS catalog)"""
    try:
        catalog.get_events(
            starttime=t0 - 600, endtime=t0 + 3600 + 600, minmagnitude=4.0,
            minlatitude=30, maxlatitude=45, minlongitude=130, maxlongitude=146)
        return False  # events were found
    except FDSNNoDataException:
        return True   # the catalog answered "nothing"


print("=== Землетрясения ===")
delays, detected, total = [], 0, 0
for o in ORIGINS:
    origin = UTCDateTime(o)
    tr = get_trace("ev_" + o[:10], origin - 900)
    if tr is None:
        continue
    total += 1
    alarms = detect(tr)
    before = [t for t in alarms if t < origin]
    hits = [t for t in alarms if origin <= t <= origin + DETECT_WINDOW]
    if hits:
        detected += 1
        delay = round(hits[0] - origin)
        delays.append(delay)
        status = f"замечено через {delay} сек"
    else:
        status = "НЕ замечено"
    print(f"{o}: {status}, ложных до очага: {len(before)}")

print("\n=== Тихие часы ===")
quiet_total, quiet_false = 0, 0
for q in QUIET_CANDIDATES:
    t0 = UTCDateTime(q)
    if not is_quiet(t0):
        print(f"{q}: не тихий (есть M4+), пропускаем")
        continue
    tr = get_trace("quiet_" + q[:10], t0)
    if tr is None:
        continue
    n = len(detect(tr))
    quiet_total += 1
    quiet_false += n
    print(f"{q}: ложных тревог за час: {n}")

print("\n=== ИТОГ ===")
print(f"Замечено землетрясений: {detected} из {total}")
if delays:
    print(f"Медианная задержка: {statistics.median(delays)} сек")
print(f"Тихих часов проверено: {quiet_total}, ложных тревог в них: {quiet_false}")