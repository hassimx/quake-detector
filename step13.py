"""Шаг 13: сравниваем правила подтверждения тревог MAJO на 24 тихих часах.

Правила (порог MAJO везде 10):
  R0: тревога MAJO сама по себе, без подтверждения;
  R1: подтверждена >= 1 соседней станцией (порог соседей 10);
  R2: подтверждена >= 2 соседними станциями (порог соседей 10);
  R3: подтверждена >= 2 соседними станциями (порог соседей 7).
"Подтверждена станцией" = её тревога в пределах +-60 сек от тревоги MAJO.

Что делаем:
  1) выбираем 24 "тихих" часа случайно (random.seed(42), 2016-2024) и
     проверяем их по каталогу USGS; неподходящие часы заменяем другими;
  2) для каждого часа качаем MAJO и 4 соседей, ищем тревоги MAJO и
     оцениваем их по R0-R3 (это "ложные тревоги", которые правило оставило);
  3) для 10 землетрясений (как в step12.py) считаем, сколько подтверждено
     по каждому правилу.

Детектор не меняется: загрузка и обработка берутся из step11.py и step12.py.
Скачанные записи лежат в data/ (папка в .gitignore).
Весь вывод сохраняется в results/step13_output.txt.
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
MAX_TRIES = 300            # сколько случайных часов готовы перебрать
HOUR_SEC = 3600
MIN_MAJO_SEC = 3540        # запись MAJO короче 59 минут считаем неполной
MIN_NEIGHBORS = 2          # меньше двух соседей с данными: R2/R3 не проверить
MAJO_ID = "IU.MAJO.00.BHZ"

# Рамка Японии и пороги каталога из условия задачи.
BOX = dict(minlatitude=30, maxlatitude=45, minlongitude=130, maxlongitude=146)
REGION_MIN_MAG, WORLD_MIN_MAG = 4.0, 5.5

# Правила: (название, сколько станций нужно, порог соседей).
# R0 без станций: тревога MAJO считается подтверждённой всегда.
RULES = [
    ("R0", 0, None),
    ("R1", 1, 10),
    ("R2", 2, 10),
    ("R3", 2, 7),
]
NEIGHBOR_THRS = sorted({thr for _, _, thr in RULES if thr})  # [7, 10]


# ---------------------------------------------------------------------------
# Часть 1: случайные тихие часы и проверка по каталогу USGS.
# ---------------------------------------------------------------------------
def random_hours(count):
    """Бесконечный список случайных начал часа (UTC), без повторов."""
    first = UTCDateTime(YEARS[0], 1, 1)
    total = int((UTCDateTime(YEARS[1] + 1, 1, 1) - first) // HOUR_SEC)
    seen = set()
    while len(seen) < count:
        k = random.randrange(total)
        if k not in seen:
            seen.add(k)
            yield first + k * HOUR_SEC


def catalog_is_quiet(catalog, t0):
    """(True, "") если в каталоге тихо; иначе (False, причина).

    "Каталог ответил: ничего нет" и "запрос сломался" - разные вещи: при
    ошибке запроса час НЕ считаем тихим.
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
            continue  # каталог ответил "ничего нет"
        except Exception as err:
            return False, f"ОШИБКА КАТАЛОГА ({err})"
        return False, f"{what}: {len(events)} соб."
    return True, ""


# ---------------------------------------------------------------------------
# Часть 2: загрузка с кэшем в data/ и детектор.
# ---------------------------------------------------------------------------
def cache_path(key, tag):
    return os.path.join(DATA_DIR, f"{key}_{tag}.mseed")


def load_neighbor(client, key, start, end, tag):
    """Запись соседа: из data/, а если там нет, скачиваем и сохраняем.

    Возвращает (трасса, None) или (None, причина).
    """
    path = cache_path(key, tag)
    if os.path.exists(path):
        return read(path)[0], None
    tr, note = neighbor_trace(client, key, start, end)
    if tr is not None:
        tr.write(path, format="MSEED")
    return tr, note


def load_majo_hour(client, start, end, tag):
    """Запись MAJO на час, строго IU.MAJO.00.BHZ. Возвращает (трасса, причина)."""
    path = cache_path("IU.MAJO", tag)
    if os.path.exists(path):
        tr = read(path)[0]
    else:
        tr, note = fetch_trace(client, "IU.MAJO", "BHZ", start, end)
        if tr is None:
            return None, note
        if tr.id != MAJO_ID:
            # fetch_trace при отсутствии location 00 берёт любую запись;
            # детектор же настроен на 00.BHZ, поэтому такой час не годится.
            return None, f"нет записи 00.BHZ (нашлась {tr.id})"
        tr.write(path, format="MSEED")
    seconds = tr.stats.npts / tr.stats.sampling_rate
    if seconds < MIN_MAJO_SEC:
        return None, f"запись MAJO неполная ({seconds:.0f} с из {HOUR_SEC})"
    return tr, None


def analyze_neighbors(report, client, start, end, tag):
    """Соседи с данными: {станция: (трасса, STA/LTA, {порог: тревоги})}."""
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


# ---------------------------------------------------------------------------
# Часть 3: оценка тревоги по правилам.
# ---------------------------------------------------------------------------
def judge(stations, alarm, need, thr):
    """Результат правила для одной тревоги MAJO.

    Станции без данных не считаются ни за, ни против: смотрим только те,
    чья запись покрывает окно +-60 сек вокруг тревоги.
      "подтверждена" - сработали >= need станций;
      "нет данных"   - покрытых станций меньше need, проверить нельзя;
      "отсеяна"      - станций хватало, но сработало меньше need.
    """
    if need == 0:
        return "подтверждена"  # R0: тревога MAJO принимается как есть
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

    # --- 1-2. Тихие часы ---
    report.line(f"=== Тихие часы: случайно (seed 42), {YEARS[0]}-{YEARS[1]}, "
                f"нужно {N_HOURS} ===")
    quiet = []  # (час, тревоги MAJO, соседи)
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

    # --- 3. Землетрясения ---
    report.line("=== Землетрясения (запись от -15 до +10 минут вокруг очага) ===")
    quakes = []  # (дата, соседи, тревога MAJO или None)
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
        # Как в step7/step12: землетрясение "замечено", если MAJO дала тревогу
        # в течение 2 минут после очага; проверяем именно эту первую тревогу.
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

    # --- 4. Итоговая таблица ---
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
