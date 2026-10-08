"""step 6: show ALL raw alarms, without a pause (cooldown)"""

from obspy import read, UTCDateTime
from obspy.signal.trigger import classic_sta_lta, trigger_onset

ON_THR, OFF_THR = 7, 3.5
STA_SEC, LTA_SEC = 1, 30

EVENTS = {
    "tohoku_2011": "2011-03-11T05:46:24",
    "chiba_2021": "2021-10-07T13:41:24",
    "ibaraki_2016": "2016-12-28T12:38:49",
}

for name, origin_str in EVENTS.items():
    st = read(f"{name}.mseed")
    st.merge(fill_value=0)
    tr = st[0]
    tr.detrend("demean")
    tr.filter("bandpass", freqmin=1.0, freqmax=8.0)
    fs = tr.stats.sampling_rate
    ratio = classic_sta_lta(tr.data, int(STA_SEC * fs), int(LTA_SEC * fs))
    origin = UTCDateTime(origin_str)

    print(f"\n{name}:")
    for on, off in trigger_onset(ratio, ON_THR, OFF_THR):
        t = tr.stats.starttime + on / fs
        print(f"  тревога через {round(t - origin)} сек от очага "
              f"(минус = до очага)")