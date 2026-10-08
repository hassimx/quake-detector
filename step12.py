"""step 12: does the rule "a MAJO alarm is confirmed by a neighbor station" work?

Rule: an alarm on IU.MAJO is considered confirmed if at least one of the
4 neighbor stations (JP.JGF, JP.JSD, G.INU, PS.TSK) gave its own alarm
within +-60 s of it.

What we check:
  1) 10 earthquakes (origin dates from step7.py), a record from -15 to +10
     minutes around the origin: how many of them the rule confirms;
  2) 3 quiet hours: how many MAJO alarms the rule keeps (these are false alarms
     the rule did not filter out) and how many it filters out.
The MAJO threshold stays 10 (as in step11). For the neighbors we try 5, 7 and 10.

The detector is not changed: loading and processing come from step11.py (by
import). step11.analyze computes the STA/LTA curve, and the alarms for
different neighbor thresholds are obtained from it again (the same STA 1 s /
LTA 30 s windows, 1-8 Hz filter, the same switch-off threshold = threshold / 2,
the same merging of alarms closer than 60 s).
All output is saved to results/step12_output.txt.
"""

import os

from obspy import UTCDateTime, read
from obspy.clients.fdsn import Client
from obspy.signal.trigger import trigger_onset

from step11 import GROUP_SEC, MATCH_SEC, ON_THR, Report, analyze, fetch_trace

OUTPUT_FILE = os.path.join("results", "step12_output.txt")

# neighbor stations and their vertical channel (as step11 found)
NEIGHBORS = {
    "JP.JGF": "BHZ",
    "JP.JSD": "BHZ",
    "G.INU": "BHZ",
    "PS.TSK": "HHZ",
}
NEIGHBOR_THRESHOLDS = (5, 7, 10)  # thresholds we try for the neighbors

# origin times (UTC) copied from step7.py: that file cannot be imported
# (it runs completely on import: downloads data and prints the results)
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
QUIET_HOURS = [
    "2019-08-15T04:00:00",
    "2019-09-20T10:00:00",
    "2018-03-12T02:00:00",
]
BEFORE, AFTER = 15 * 60, 10 * 60  # window around the origin: -15 ... +10 minutes
QUIET_SEC = 3600                  # a quiet hour lasts exactly one hour
EQ_DETECT_SEC = 120               # MAJO "detected" the earthquake (as in step7)
COVER_TOL = 2                     # tolerance when checking record coverage, s


def majo_window(name, start, end):
    """MAJO record from the file that is already in the repository (as in step7)"""
    st = read(f"{name}.mseed")
    st.merge(fill_value=0)
    tr = st[0]
    tr.trim(start, end)
    return tr


def neighbor_trace(client, key, start, end):
    """neighbor record: the needed channel first, then the second one (BHZ/HHZ).

    Returns (trace, None) or (None, reason). Does not raise.
    """
    first = NEIGHBORS[key]
    other = "HHZ" if first == "BHZ" else "BHZ"
    reasons = []
    for channel in (first, other):
        tr, note = fetch_trace(client, key, channel, start, end)
        if tr is not None:
            return tr, None
        reasons.append(f"{channel}: {note}")
    return None, "; ".join(reasons)


def alarms_at(tr, ratio, thr):
    """alarms from a ready STA/LTA curve at a given threshold (switch-off threshold = thr/2)"""
    fs = tr.stats.sampling_rate
    alarms = []
    for on, _off in trigger_onset(ratio, thr, thr / 2):
        t = tr.stats.starttime + on / fs
        if not alarms or t - alarms[-1] > GROUP_SEC:
            alarms.append(t)
    return alarms


def covers(tr, alarm):
    """does the record cover the whole +-60 s interval around the alarm"""
    return (tr.stats.starttime <= alarm - MATCH_SEC + COVER_TOL
            and tr.stats.endtime >= alarm + MATCH_SEC - COVER_TOL)


def process(report, client, label, majo_tr, start, end):
    """one segment (an earthquake or a quiet hour): MAJO + 4 neighbors.

    Returns a dict: the MAJO alarms and the STA/LTA curve of every neighbor.
    """
    _, _, majo_alarms = analyze(majo_tr)  # threshold 10, as in step11
    report.line(f"  MAJO: запись {majo_tr.stats.npts / majo_tr.stats.sampling_rate:.0f} с, "
                f"тревог (порог {ON_THR}): {len(majo_alarms)}")
    stations = {}
    for key in NEIGHBORS:
        tr, note = neighbor_trace(client, key, start, end)
        if tr is None:
            report.line(f"  {key}: НЕТ ДАННЫХ ({note})")
            continue
        tr_f, ratio, _ = analyze(tr)
        seconds = tr.stats.npts / tr.stats.sampling_rate
        stations[key] = (tr_f, ratio)
        report.line(f"  {key}: {tr.id}, {tr.stats.sampling_rate:g} Гц, "
                    f"записи {seconds:.0f} с из {end - start:.0f}")
    return dict(majo=majo_alarms, stations=stations)


def judge(data, alarm, thr):
    """status of a MAJO alarm at neighbor threshold thr.

    "подтверждена" (confirmed)  - at least one neighbor station fired within +-60 s;
    "отсеяна" (filtered out)    - neighbor data was there, but nobody fired;
    "нет данных" (no data)      - no neighbor station covers the +-60 s window.
    """
    judged = False
    for tr, ratio in data["stations"].values():
        if not covers(tr, alarm):
            continue
        judged = True
        if any(abs(t - alarm) <= MATCH_SEC for t in alarms_at(tr, ratio, thr)):
            return "подтверждена"
    return "отсеяна" if judged else "нет данных"


def main():
    report = Report(OUTPUT_FILE)
    client = Client("EARTHSCOPE", timeout=120)
    report.line(f"Правило: тревога MAJO (порог {ON_THR}) подтверждена, если одна из "
                f"{len(NEIGHBORS)} соседних станций ({', '.join(NEIGHBORS)}) "
                f"сработала в пределах +-{MATCH_SEC} с.")
    report.line(f"Пороги соседей: {', '.join(map(str, NEIGHBOR_THRESHOLDS))}")
    report.line()

    # 1. earthquakes
    report.line("=== Землетрясения (запись от -15 до +10 минут вокруг очага) ===")
    quakes = []  # (date, data, MAJO alarm, the alarm that detected the earthquake or None)
    for text in ORIGINS:
        origin = UTCDateTime(text)
        report.line(f"{text}")
        start, end = origin - BEFORE, origin + AFTER
        try:
            majo_tr = majo_window("ev_" + text[:10], start, end)
        except Exception as err:
            report.line(f"  MAJO: не удалось прочитать запись: {err}")
            continue
        data = process(report, client, text, majo_tr, start, end)
        # the alarm by which MAJO "detected" the earthquake: the first one in 0..+120 s
        # after the origin (the same rule as in step7)
        hits = [t for t in data["majo"] if origin <= t <= origin + EQ_DETECT_SEC]
        quakes.append((text, data, hits[0] if hits else None))
        if hits:
            report.line(f"  MAJO заметила землетрясение через "
                        f"{round(hits[0] - origin)} с")
        else:
            report.line("  MAJO НЕ заметила землетрясение (порог 10)")
    report.line()

    # 2. quiet hours
    report.line("=== Тихие часы (по часу) ===")
    quiet = []  # (hour, data)
    for text in QUIET_HOURS:
        t0 = UTCDateTime(text)
        report.line(f"{text}")
        try:
            majo_tr = majo_window("quiet_" + text[:10], t0, t0 + QUIET_SEC)
        except Exception as err:
            report.line(f"  MAJO: не удалось прочитать запись: {err}")
            continue
        quiet.append((text, process(report, client, text, majo_tr,
                                    t0, t0 + QUIET_SEC)))
    report.line()

    # 3. tables by threshold
    n_quakes = len(quakes)
    majo_detected = sum(1 for _, _, a in quakes if a is not None)
    report.line(f"Землетрясений обработано: {n_quakes} из {len(ORIGINS)}; "
                f"MAJO заметила (порог {ON_THR}): {majo_detected}")
    quiet_alarms = [(h, a, d) for h, d in quiet for a in d["majo"]]
    report.line(f"Тихих часов обработано: {len(quiet)} из {len(QUIET_HOURS)}; "
                f"тревог MAJO в них (порог {ON_THR}): {len(quiet_alarms)}")
    report.line()

    for thr in NEIGHBOR_THRESHOLDS:
        report.line(f"=== Порог соседей {thr} (порог MAJO {ON_THR}) ===")
        q_status = [judge(d, a, thr) for _, d, a in quakes if a is not None]
        report.line(f"  Землетрясения, подтверждены хотя бы одной соседней "
                    f"станцией: {q_status.count('подтверждена')} из {n_quakes}")
        report.line(f"    не подтверждены, данные соседей были: "
                    f"{q_status.count('отсеяна')}")
        report.line(f"    не с чем сравнить (у соседей нет данных): "
                    f"{q_status.count('нет данных')}")
        report.line(f"    MAJO само не заметила землетрясение: "
                    f"{n_quakes - majo_detected}")
        s = [judge(d, a, thr) for _, a, d in quiet_alarms]
        report.line(f"  Тревоги MAJO в тихих часах (всего {len(s)}):")
        report.line(f"    остались подтверждёнными (ложные не отсеяны): "
                    f"{s.count('подтверждена')}")
        report.line(f"    отсеяны: {s.count('отсеяна')}")
        report.line(f"    не с чем сравнить (у соседей нет данных): "
                    f"{s.count('нет данных')}")
        for hour, alarm, d in quiet_alarms:
            report.line(f"      {str(alarm)[:19]}  {judge(d, alarm, thr)}")
        report.line()

    report.line(f"Вывод сохранён в {OUTPUT_FILE}")
    report.close()


if __name__ == "__main__":
    main()
