"""step 11: do the neighbor stations see the MAJO alarms?

Idea: a real earthquake is recorded by several close stations at once,
while a local disturbance (wind, a sensor glitch) is usually seen on one only.
So for every alarm on IU.MAJO we:
  1) find the neighbor seismic stations in a box around Japan;
  2) download 5 minutes of record (from -2 to +3 minutes) from the 3 closest;
  3) run them through the SAME detector as for MAJO;
  4) check whether they fired within +-60 s of the MAJO alarm.

The detector is NOT changed here: the parameters and processing are copied from
step.7.py (it runs completely on import and has a dot in its name, so it cannot
be imported). All output is saved to results/step11_output.txt, the pictures
are next to it, in results/.
"""

import os

import matplotlib

matplotlib.use("Agg")  # draw to files, without a window
import matplotlib.pyplot as plt

from obspy import UTCDateTime
from obspy.clients.fdsn import Client
from obspy.clients.fdsn.header import FDSNNoDataException
from obspy.geodetics import gps2dist_azimuth
from obspy.signal.trigger import classic_sta_lta, trigger_onset

# detector parameters (as in step.7.py / step8.py; threshold 10 as the task says)
ON_THR = 10              # STA/LTA switch-on threshold
OFF_THR = ON_THR / 2     # switch-off threshold (as in step7/step8: threshold / 2)
STA_SEC, LTA_SEC = 1, 30  # short and long windows, seconds
FREQ_MIN, FREQ_MAX = 1.0, 8.0  # band-pass filter, Hz
GROUP_SEC = 60           # alarms closer than 60 s count as one

# what we are looking for
BOX = dict(minlatitude=30, maxlatitude=45, minlongitude=130, maxlongitude=146)
DATES = ["2019-09-20", "2018-03-12"]  # the station must work on both dates
CHANNELS = ("BHZ", "HHZ")             # vertical component
MAJO = "IU.MAJO"
N_NEIGHBORS = 6                       # how many nearest stations we take
# (was 3, but IM.MJAR, 0.6 km from MAJO, has no records for these dates,
# so we take more to have something left to compare with)

ALARMS = [
    "2019-09-20T10:48:45",
    "2019-09-20T10:50:00",
    "2019-09-20T10:54:09",
    "2018-03-12T02:45:07",
]
BEFORE, AFTER = 120, 180   # record: from -2 to +3 minutes around the alarm
MATCH_SEC = 60             # "fired nearby" if the alarm is within +-60 s

RESULTS_DIR = "results"
OUTPUT_FILE = os.path.join(RESULTS_DIR, "step11_output.txt")


class Report:
    """prints a line to the screen and writes it to the file at the same time"""

    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.file = open(path, "w", encoding="utf-8")

    def line(self, text=""):
        print(text)
        self.file.write(text + "\n")
        self.file.flush()  # so that what was already printed is saved after a crash

    def close(self):
        self.file.close()


# part 1: finding stations
def stations_on_date(client, date):
    """stations with BHZ/HHZ in the box that worked on this day.

    Returns a dict {"network.station": {lat, lon, channels}}.
    If the catalog answers "nothing", an empty dict.
    """
    start = UTCDateTime(date)
    try:
        inv = client.get_stations(
            network="*", station="*", channel=",".join(CHANNELS),
            starttime=start, endtime=start + 86400, level="channel", **BOX)
    except FDSNNoDataException:
        return {}
    found = {}
    for net in inv:
        for sta in net:
            codes = sorted({cha.code for cha in sta if cha.code in CHANNELS})
            if codes:
                found[f"{net.code}.{sta.code}"] = dict(
                    lat=sta.latitude, lon=sta.longitude, channels=codes)
    return found


def find_stations(client):
    """stations active on ALL required dates (intersection over dates)"""
    per_date = [stations_on_date(client, d) for d in DATES]
    common = {}
    for key, info in per_date[0].items():
        if not all(key in found for found in per_date):
            continue
        channels = set(info["channels"])
        for found in per_date[1:]:
            channels &= set(found[key]["channels"])
        if channels:
            common[key] = dict(info, channels=sorted(channels))
    return common


def majo_position(client, stations):
    """coordinates of MAJO: from the found stations or by a separate query"""
    if MAJO in stations:
        return stations[MAJO]["lat"], stations[MAJO]["lon"]
    net, sta = MAJO.split(".")
    inv = client.get_stations(network=net, station=sta, level="station")
    return inv[0][0].latitude, inv[0][0].longitude


def distance_km(lat1, lon1, lat2, lon2):
    """distance over the Earth's surface, km"""
    return gps2dist_azimuth(lat1, lon1, lat2, lon2)[0] / 1000


def pick_channel(channels):
    """we prefer BHZ (like MAJO), otherwise HHZ"""
    return "BHZ" if "BHZ" in channels else channels[0]


# part 2: download and detector
def fetch_trace(client, key, channel, start, end):
    """downloads a station record. Returns (trace, None) or (None, reason).

    "No data" and "the request broke" are different reasons, they must not be confused.
    """
    net, sta = key.split(".")
    try:
        # as in step2/step7: location "00" first, and if there is none, any ("*")
        # Otherwise MAJO would get another record (10.BHZ, 40 Hz), not the one
        # the detector was built on (00.BHZ, 20 Hz)
        try:
            st = client.get_waveforms(net, sta, "00", channel, start, end)
        except FDSNNoDataException:
            st = client.get_waveforms(net, sta, "*", channel, start, end)
        st.merge(fill_value=0)              # as in step7: fill gaps with zeros
        tr = max(st, key=lambda t: t.stats.npts)  # the longest trace
    except FDSNNoDataException:
        return None, "нет данных"
    except Exception as err:
        return None, f"ОШИБКА ЗАГРУЗКИ: {err}"
    if tr.stats.npts / tr.stats.sampling_rate < 2 * LTA_SEC:
        return None, "запись слишком короткая для окна LTA"
    return tr, None


def analyze(tr):
    """the same detector as in step.7.py (the detect function).

    There is only one difference: we also return the filtered signal with the
    STA/LTA curve (for the plots and peaks), and do not spoil the source trace.
    """
    tr = tr.copy()
    tr.detrend("demean")
    tr.filter("bandpass", freqmin=FREQ_MIN, freqmax=FREQ_MAX)
    fs = tr.stats.sampling_rate
    ratio = classic_sta_lta(tr.data, int(STA_SEC * fs), int(LTA_SEC * fs))
    alarms = []
    for on, off in trigger_onset(ratio, ON_THR, OFF_THR):
        t = tr.stats.starttime + on / fs
        if not alarms or t - alarms[-1] > GROUP_SEC:
            alarms.append(t)
    return tr, ratio, alarms


# part 3: comparison with the alarm on MAJO
def peak_near(tr, ratio, alarm):
    """STA/LTA maximum in the +-60 s window around the alarm (None if there is no window)"""
    fs = tr.stats.sampling_rate
    i0 = int((alarm - MATCH_SEC - tr.stats.starttime) * fs)
    i1 = int((alarm + MATCH_SEC - tr.stats.starttime) * fs)
    i0, i1 = max(i0, 0), min(i1, len(ratio))
    if i1 <= i0:
        return None
    return float(ratio[i0:i1].max())


def compare(tr, ratio, alarms, alarm):
    """result for one station: did it fire within +-60 s"""
    near = [t for t in alarms if abs(t - alarm) <= MATCH_SEC]
    return dict(
        hit=bool(near),
        shift=round(near[0] - alarm) if near else None,
        peak=peak_near(tr, ratio, alarm),
        alarms=alarms,
    )


def plot_alarm(path, alarm, rows):
    """signals of all stations one under another (after the 1-8 Hz filter)"""
    fig, axes = plt.subplots(len(rows), 1, figsize=(10, 2.2 * len(rows)),
                             sharex=True, squeeze=False)
    for ax, row in zip(axes[:, 0], rows):
        if row["tr"] is None:
            ax.text(0.5, 0.5, f'{row["key"]}: {row["note"]}',
                    ha="center", va="center", transform=ax.transAxes)
            ax.set_yticks([])  # a scale on an empty plot is pointless
        else:
            tr = row["tr"]
            t = tr.times() + (tr.stats.starttime - alarm)  # seconds from the MAJO alarm
            ax.plot(t, tr.data, linewidth=0.5)
            for a in row["alarms"]:
                ax.axvline(a - alarm, color="orange", linestyle="--",
                           linewidth=1)
            ax.set_ylabel(row["key"], fontsize=8)
        ax.axvspan(-MATCH_SEC, MATCH_SEC, color="red", alpha=0.08)
        ax.axvline(0, color="red", linewidth=1)
    axes[-1, 0].set_xlabel("секунды от тревоги на MAJO "
                           "(оранжевое: тревога станции, розовая зона: +-60 с)")
    axes[0, 0].set_title(f"Тревога MAJO {alarm}  (сигнал после фильтра "
                         f"{FREQ_MIN:g}-{FREQ_MAX:g} Гц, в отсчётах датчика)")
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


def main():
    report = Report(OUTPUT_FILE)
    client = Client("EARTHSCOPE", timeout=120)

    report.line("Параметры детектора: "
                f"фильтр {FREQ_MIN:g}-{FREQ_MAX:g} Гц, STA {STA_SEC} с, "
                f"LTA {LTA_SEC} с, порог {ON_THR}/{OFF_THR:g}, "
                f"склейка тревог {GROUP_SEC} с")
    report.line()

    # 1. stations
    report.line("=== Станции (BHZ/HHZ, рамка 30-45 с.ш., 130-146 в.д., "
                f"активны {' и '.join(DATES)}) ===")
    try:
        stations = find_stations(client)
        lat0, lon0 = majo_position(client, stations)
    except Exception as err:
        report.line(f"ОШИБКА ЗАПРОСА СТАНЦИЙ (это не 'ничего нет'): {err}")
        report.close()
        return

    for info in stations.values():
        info["dist"] = distance_km(lat0, lon0, info["lat"], info["lon"])
    ordered = sorted(stations.items(), key=lambda kv: kv[1]["dist"])
    if not ordered:
        report.line("ничего не найдено")
    for key, info in ordered:
        mark = "  <- опорная станция" if key == MAJO else ""
        report.line(f"  {key:<10} широта {info['lat']:8.4f}  "
                    f"долгота {info['lon']:9.4f}  "
                    f"до MAJO {info['dist']:7.1f} км  "
                    f"каналы {','.join(info['channels'])}{mark}")
    report.line()

    neighbors = [(k, v) for k, v in ordered if k != MAJO][:N_NEIGHBORS]
    if not neighbors:
        report.line("Соседних станций нет, сравнивать не с чем.")
        report.close()
        return
    report.line(f"Берём {len(neighbors)} ближайших: "
                + ", ".join(f"{k} ({v['dist']:.0f} км)" for k, v in neighbors))
    report.line()

    # we download MAJO the same way as the neighbors: to compare on one picture
    majo_channel = pick_channel(stations[MAJO]["channels"]) \
        if MAJO in stations else "BHZ"
    targets = [(MAJO, majo_channel)] + [
        (k, pick_channel(v["channels"])) for k, v in neighbors]

    # 2-3. alarms
    summary = []  # (alarm, {station: short status})
    for number, alarm_text in enumerate(ALARMS, start=1):
        alarm = UTCDateTime(alarm_text)
        report.line(f"=== Тревога {number}: {alarm} ===")
        start, end = alarm - BEFORE, alarm + AFTER
        rows, status = [], {}

        for key, channel in targets:
            tr, note = fetch_trace(client, key, channel, start, end)
            if tr is None:
                report.line(f"  {key}: {note}")
                rows.append(dict(key=key, tr=None, alarms=[], note=note))
                status[key] = "нет данных" if note == "нет данных" \
                    else "ошибка"
                continue

            seconds = tr.stats.npts / tr.stats.sampling_rate
            tr_f, ratio, alarms = analyze(tr)
            res = compare(tr_f, ratio, alarms, alarm)
            peak = "?" if res["peak"] is None else f"{res['peak']:.1f}"
            head = f"  {key} ({tr.id}, {tr.stats.sampling_rate:g} Гц, " \
                   f"записи {seconds:.0f} с из {BEFORE + AFTER})"
            if res["hit"]:
                report.line(f"{head}: СРАБОТАЛА, тревога {res['shift']:+d} с "
                            f"от MAJO-тревоги, пик STA/LTA в окне {peak}")
                status[key] = f"да ({res['shift']:+d} с)"
            else:
                report.line(f"{head}: не сработала, пик STA/LTA в окне "
                            f"+-{MATCH_SEC} с = {peak} (порог {ON_THR})")
                status[key] = "нет"
            # all firings of the station in the 5-minute record (seconds from the MAJO
            # alarm), including outside the +-60 s window: this shows why it says "no"
            own = ", ".join(f"{round(a - alarm):+d}" for a in alarms) or "нет"
            report.line(f"      все срабатывания станции в записи, сек от "
                        f"тревоги MAJO: {own}")
            rows.append(dict(key=key, tr=tr_f, alarms=alarms, note=""))

        stamp = alarm.strftime("%Y%m%dT%H%M%S")
        path = os.path.join(RESULTS_DIR, f"step11_alarm{number}_{stamp}.png")
        plot_alarm(path, alarm, rows)
        report.line(f"  картинка: {path}")
        report.line()
        summary.append((alarm, status))

    # summary table
    report.line("=== Сводка: сработала ли станция в пределах "
                f"+-{MATCH_SEC} с ===")
    names = [k for k, _ in targets]
    report.line("  " + "тревога".ljust(22) + "".join(n.ljust(16) for n in names))
    for alarm, status in summary:
        report.line("  " + str(alarm)[:19].ljust(22)
                    + "".join(status.get(n, "?").ljust(16) for n in names))
    report.line()
    report.line(f"Вывод сохранён в {OUTPUT_FILE}")
    report.close()


if __name__ == "__main__":
    main()
