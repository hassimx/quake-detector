"""Шаг 5: проверяем детектор на нескольких землетрясениях."""

import os

from obspy import read, UTCDateTime
from obspy.clients.fdsn import Client
from obspy.signal.trigger import classic_sta_lta, trigger_onset

# Настройки, которые выбрали на шаге 4.
ON_THR, OFF_THR = 7, 3.5
STA_SEC, LTA_SEC = 1, 30
COOLDOWN = 600  # после тревоги молчим 10 минут

# start = с какого времени берём запись (за 15 минут до землетрясения)
# origin = когда на самом деле случилось землетрясение (UTC)
EVENTS = [
    {"name": "tohoku_2011", "start": "2011-03-11T05:30:00",
     "origin": "2011-03-11T05:46:24"},
    {"name": "chiba_2021", "start": "2021-10-07T13:25:00",
     "origin": "2021-10-07T13:41:24"},
    {"name": "ibaraki_2016", "start": "2016-12-28T12:23:00",
     "origin": "2016-12-28T12:38:49"},
]

client = Client("EARTHSCOPE")


def get_trace(name, start):
    """Берём запись из файла, а если файла нет, скачиваем из интернета."""
    path = f"{name}.mseed"
    if not os.path.exists(path):
        t0 = UTCDateTime(start)
        try:
            st = client.get_waveforms("IU", "MAJO", "00", "BHZ", t0, t0 + 3600)
        except Exception:
            st = client.get_waveforms("IU", "MAJO", "*", "BHZ", t0, t0 + 3600)
        st.write(path, format="MSEED")
    st = read(path)
    st.merge(fill_value=0)
    return st[0]


def detect(tr):
    """Возвращает список времён тревог."""
    tr.detrend("demean")
    tr.filter("bandpass", freqmin=1.0, freqmax=8.0)
    fs = tr.stats.sampling_rate
    ratio = classic_sta_lta(tr.data, int(STA_SEC * fs), int(LTA_SEC * fs))
    alarms = []
    for on, off in trigger_onset(ratio, ON_THR, OFF_THR):
        t = tr.stats.starttime + on / fs
        if not alarms or t - alarms[-1] > COOLDOWN:
            alarms.append(t)
    return alarms


for ev in EVENTS:
    tr = get_trace(ev["name"], ev["start"])
    alarms = detect(tr)
    origin = UTCDateTime(ev["origin"])
    false_n = len([t for t in alarms if t < origin])
    real = [t for t in alarms if t >= origin]
    delay = round(real[0] - origin) if real else "не заметила"
    print(f'{ev["name"]}: ложных {false_n}, '
          f'первая тревога через {delay} сек после очага')