"""step 13: compare the rules for confirming MAJO alarms on 24 quiet hours.

Rules (the MAJO threshold is 10 everywhere):
  R0: a MAJO alarm by itself, without confirmation;
  R1: confirmed by >= 1 neighbor station (neighbor threshold 10);
  R2: confirmed by >= 2 neighbor stations (neighbor threshold 10);
  R3: confirmed by >= 2 neighbor stations (neighbor threshold 7).
"Confirmed by a station" = its alarm within +-60 s of the MAJO alarm.

What we do:
  1) pick 24 "quiet" hours at random (random.seed(42), 2016-2024) and
     check them against the USGS catalog; unsuitable hours are replaced by others;
  2) for every hour we download MAJO and 4 neighbors, find the MAJO alarms and
     rate them by R0-R3 (these are "false alarms" that the rule kept);
  3) for the 10 earthquakes (as in step12.py) we count how many are confirmed
     by each rule.

The detector is not changed: loading and processing come from step11.py and
step12.py. The downloaded records are in data/ (the folder is in .gitignore).
All output is saved to results/step13_output.txt.
"""

import os
import random

from obspy import UTCDateTime, read
from obspy.clients.fdsn import Client
from obspy.clients.fdsn.header import FDSNNoDataException

from step11 import MATCH_SEC, ON_THR, Report, analyze, fetch_trace
from step12 import (AFTER, BEFORE, EQ_DETECT_SEC, NEIGHBORS, ORIGINS,
                    alarms_at, covers, majo_window, neighbor_trace)

OUTPUT_FILE = os.path.join("results", "step13_output.txt")
DATA_DIR = "data"

N_HOURS = 24
YEARS = (2016, 2024)
MAX_TRIES = 300            # how many random hours we are ready to try
HOUR_SEC = 3600
MIN_MAJO_SEC = 3540        # a MAJO record shorter than 59 minutes counts as incomplete
MIN_NEIGHBORS = 2          # fewer than two neighbors with data: R2/R3 cannot be checked
MAJO_ID = "IU.MAJO.00.BHZ"

# Japan box and catalog thresholds from the task
BOX = dict(minlatitude=30, maxlatitude=45, minlongitude=130, maxlongitude=146)
REGION_MIN_MAG, WORLD_MIN_MAG = 4.0, 5.5

# rules: (name, how many stations are needed, neighbor threshold)
# R0 has no stations: a MAJO alarm always counts as confirmed
RULES = [
    ("R0", 0, None),
    ("R1", 1, 10),
    ("R2", 2, 10),
    ("R3", 2, 7),
]
NEIGHBOR_THRS = sorted({thr for _, _, thr in RULES if thr})  # [7, 10]


# part 1: random quiet hours and the check against the USGS catalog
def random_hours(count):
    """endless list of random hour starts (UTC), without repeats"""
    first = UTCDateTime(YEARS[0], 1, 1)
    total = int((UTCDateTime(YEARS[1] + 1, 1, 1) - first) // HOUR_SEC)
    seen = set()
    while len(seen) < count:
        k = random.randrange(total)
        if k not in seen:
            seen.add(k)
            yield first + k * HOUR_SEC


def catalog_is_quiet(catalog, t0):
    """(True, "") if the catalog is quiet; otherwise (False, reason).

    "The catalog answered: nothing" and "the request broke" are different things:
    on a request error the hour is NOT counted as quiet.
    """
    checks = [
        (f"в Японии M{REGION_MIN_MAG:g}+ в окне -10..+70 мин",
         dict(starttime=t0 - 600, endtime=t0 + HOUR_SEC + 600,
              minmagnitude=REGION_MIN_MAG, **BOX)),
        (f"в мире M{WORLD_MIN_MAG:g}+ за 30 мин до часа",
         dict(starttime=t0 - 1800, endtime=t0, minmagnitude=WORLD_MIN_MAG)),
    ]
    for what, query in checks:
        try:
            events = catalog.get_events(**query)
        except FDSNNoDataException:
            continue  # the catalog answered "nothing"
        except Exception as err:
            return False, f"ОШИБКА КАТАЛОГА ({err})"
        return False, f"{what}: {len(events)} соб."
    return True, ""


# part 2: loading with a cache in data/ and the detector
def cache_path(key, tag):
    return os.path.join(DATA_DIR, f"{key}_{tag}.mseed")


def load_neighbor(client, key, start, end, tag):
    """neighbor record: from data/, and if it is not there, download and save it.

    Returns (trace, None) or (None, reason).
    """
    path = cache_path(key, tag)
    if os.path.exists(path):
        return read(path)[0], None
    tr, note = neighbor_trace(client, key, start, end)
    if tr is not None:
        tr.write(path, format="MSEED")
    return tr, note


def load_majo_hour(client, start, end, tag):
    """MAJO record for the hour, strictly IU.MAJO.00.BHZ. Returns (trace, reason)"""
    path = cache_path("IU.MAJO", tag)
    if os.path.exists(path):
        tr = read(path)[0]
    else:
        tr, note = fetch_trace(client, "IU.MAJO", "BHZ", start, end)
        if tr is None:
            return None, note
        if tr.id != MAJO_ID:
            # fetch_trace takes any record when location 00 is missing;
            # the detector is tuned to 00.BHZ, so such an hour does not fit
            return None, f"нет записи 00.BHZ (нашлась {tr.id})"
        tr.write(path, format="MSEED")
    seconds = tr.stats.npts / tr.stats.sampling_rate
    if seconds < MIN_MAJO_SEC:
        return None, f"запись MAJO неполная ({seconds:.0f} с из {HOUR_SEC})"
    return tr, None


def analyze_neighbors(report, client, start, end, tag):
    """neighbors with data: {station: (trace, STA/LTA, {threshold: alarms})}"""
    stations = {}
    for key in NEIGHBORS:
        tr, note = load_neighbor(client, key, start, end, tag)
        if tr is None:
            report.line(f"    {key}: НЕТ ДАННЫХ ({note})")
            continue
        tr_f, ratio, _ = analyze(tr)
        alarms = {thr: alarms_at(tr_f, ratio, thr) for thr in NEIGHBOR_THRS}
        stations[key] = (tr_f, ratio, alarms)
        seconds = tr.stats.npts / tr.stats.sampling_rate
        report.line(f"    {key}: {tr.id}, {tr.stats.sampling_rate:g} Гц, "
                    f"записи {seconds:.0f} с из {end - start:.0f}")
    return stations


# part 3: rating an alarm by the rules
def judge(stations, alarm, need, thr):
    """result of a rule for one MAJO alarm.

    Stations without data count neither for nor against: we only look at those
    whose record covers the +-60 s window around the alarm.
      "подтверждена" (confirmed)  - >= need stations fired;
      "нет данных" (no data)      - fewer than need stations are covered, cannot check;
      "отсеяна" (filtered out)    - there were enough stations, but fewer than need fired.
    """
    if need == 0:
        return "подтверждена"  # R0: a MAJO alarm is accepted as it is
    covered = hits = 0
    for tr, _ratio, alarms in stations.values():
        if not covers(tr, alarm):
            continue
        covered += 1
        if any(abs(t - alarm) <= MATCH_SEC for t in alarms[thr]):
            hits += 1
    if hits >= need:
        return "подтверждена"
    if covered < need:
        return "нет данных"
    return "отсеяна"


def count(statuses):
    return {s: statuses.count(s)
            for s in ("подтверждена", "отсеяна", "нет данных")}


def main():
    random.seed(42)
    os.makedirs(DATA_DIR, exist_ok=True)
    report = Report(OUTPUT_FILE)
    waves = Client("EARTHSCOPE", timeout=120)
    catalog = Client("USGS", timeout=120)

    report.line("Правила подтверждения тревоги MAJO (порог MAJO "
                f"{ON_THR}); станция 'сработала', если её тревога в "
                f"пределах +-{MATCH_SEC} с:")
    for name, need, thr in RULES:
        what = "MAJO сама, без подтверждения" if need == 0 else \
            f">= {need} соседних станций, порог соседей {thr}"
        report.line(f"  {name}: {what}")
    report.line("Станции без данных не считаются ни за, ни против; если из-за "
                "этого правило проверить нельзя, пишем 'нет данных'.")
    report.line()

    # 1-2. quiet hours
    report.line(f"=== Тихие часы: случайно (seed 42), {YEARS[0]}-{YEARS[1]}, "
                f"нужно {N_HOURS} ===")
    quiet = []  # (hour, MAJO alarms, neighbors)
    tries = rejected_catalog = skipped_data = 0
    for t0 in random_hours(MAX_TRIES):
        if len(quiet) >= N_HOURS:
            break
        tries += 1
        label = str(t0)[:13] + ":00"
        ok, why = catalog_is_quiet(catalog, t0)
        if not ok:
            rejected_catalog += 1
            report.line(f"{label}: не тихий, заменяем ({why})")
            continue
        tag = t0.strftime("hour_%Y%m%dT%H")
        end = t0 + HOUR_SEC
        majo_tr, note = load_majo_hour(waves, t0, end, tag)
        if majo_tr is None:
            skipped_data += 1
            report.line(f"{label}: тихий по каталогу, но MAJO недоступна, "
                        f"пропускаем ({note})")
            continue
        report.line(f"{label}: тихий по каталогу")
        stations = analyze_neighbors(report, waves, t0, end, tag)
        if len(stations) < MIN_NEIGHBORS:
            skipped_data += 1
            report.line(f"  пропускаем: соседей с данными {len(stations)}, "
                        f"нужно хотя бы {MIN_NEIGHBORS}")
            continue
        majo_tr = majo_tr.copy()
        majo_tr.trim(t0, end)
        _, _, alarms = analyze(majo_tr)
        report.line(f"  принят час №{len(quiet) + 1}; тревог MAJO "
                    f"(порог {ON_THR}): {len(alarms)}")
        for alarm in alarms:
            res = {n: judge(stations, alarm, need, thr)
                   for n, need, thr in RULES}
            report.line(f"    {str(alarm)[11:19]}  "
                        + "  ".join(f"{n}:{r}" for n, r in res.items()))
        quiet.append((label, alarms, stations))
    report.line()
    report.line(f"Тихих часов принято: {len(quiet)} из {N_HOURS} нужных "
                f"(перебрано кандидатов {tries}, отклонено каталогом "
                f"{rejected_catalog}, пропущено из-за данных {skipped_data})")
    report.line()

    # 3. earthquakes
    report.line("=== Землетрясения (запись от -15 до +10 минут вокруг очага) ===")
    quakes = []  # (date, neighbors, MAJO alarm or None)
    for text in ORIGINS:
        origin = UTCDateTime(text)
        report.line(text)
        start, end = origin - BEFORE, origin + AFTER
        try:
            majo_tr = majo_window("ev_" + text[:10], start, end)
        except Exception as err:
            report.line(f"  MAJO: не удалось прочитать запись: {err}")
            continue
        _, _, alarms = analyze(majo_tr)
        # as in step7/step12: an earthquake is "detected" if MAJO gave an alarm
        # within 2 minutes after the origin; we check exactly this first alarm
        hits = [t for t in alarms if origin <= t <= origin + EQ_DETECT_SEC]
        stations = analyze_neighbors(report, waves, start, end,
                                     "quake_" + text[:10])
        if hits:
            report.line(f"  MAJO заметила землетрясение через "
                        f"{round(hits[0] - origin)} с")
        else:
            report.line("  MAJO НЕ заметила землетрясение (порог 10)")
        quakes.append((text, stations, hits[0] if hits else None))
    report.line()

    # 4. summary table
    hours = len(quiet)
    all_alarms = [(a, st) for _, alarms, st in quiet for a in alarms]
    missed = sum(1 for _, _, a in quakes if a is None)
    report.line("=== ИТОГ ===")
    report.line(f"Землетрясений: {len(quakes)} (MAJO сама не заметила: "
                f"{missed}); тихих часов: {hours}; тревог MAJO в них: "
                f"{len(all_alarms)}")
    report.line()
    head = ("  " + "правило".ljust(8) + "замечено из 10".ljust(18)
            + "(нет данных)".ljust(14) + "ложных всего".ljust(14)
            + "(нет данных)".ljust(14) + "ложных в час")
    report.line(head)
    for name, need, thr in RULES:
        q = count([judge(st, a, need, thr)
                   for _, st, a in quakes if a is not None])
        f = count([judge(st, a, need, thr) for a, st in all_alarms])
        rate = f["подтверждена"] / hours if hours else float("nan")
        report.line("  " + name.ljust(8)
                    + f"{q['подтверждена']} из {len(quakes)}".ljust(18)
                    + str(q["нет данных"]).ljust(14)
                    + str(f["подтверждена"]).ljust(14)
                    + str(f["нет данных"]).ljust(14)
                    + f"{rate:.2f}")
    report.line()
    report.line("'Замечено из 10': землетрясений, чья тревога MAJO прошла "
                "правило. 'Ложных всего': тревоги MAJO в тихих часах, которые "
                "правило оставило. '(нет данных)': случаи, где правило "
                "проверить было нельзя; в 'замечено' и 'ложных' они не входят.")
    report.line()
    report.line(f"Вывод сохранён в {OUTPUT_FILE}")
    report.close()


if __name__ == "__main__":
    main()
