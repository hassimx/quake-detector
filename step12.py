"""Шаг 12: работает ли правило "тревога MAJO подтверждена соседней станцией"?

Правило: тревога на IU.MAJO считается подтверждённой, если хотя бы одна из
4 соседних станций (JP.JGF, JP.JSD, G.INU, PS.TSK) дала свою тревогу в
пределах +-60 сек от неё.

Что проверяем:
  1) 10 землетрясений (даты очагов из step.7.py), запись от -15 до +10 минут
     вокруг очага: сколько из них правило подтверждает;
  2) 3 тихих часа: сколько тревог MAJO правило оставляет (это ложные тревоги,
     которые правило не отсекло) и сколько отсеивает.
Порог на MAJO остаётся 10 (как в step11). Для соседей пробуем 5, 7 и 10.

Детектор не меняется: загрузку и обработку берём из step11.py (импортом).
step11.analyze считает кривую STA/LTA, а тревоги для разных порогов соседей
мы получаем из неё заново (то же окно STA 1 с / LTA 30 с, фильтр 1-8 Гц,
тот же порог выключения = порог / 2, та же склейка тревог ближе 60 сек).
Весь вывод сохраняется в results/step12_output.txt.
"""

import os

from obspy import UTCDateTime, read
from obspy.clients.fdsn import Client
from obspy.signal.trigger import trigger_onset

from step11 import GROUP_SEC, MATCH_SEC, ON_THR, Report, analyze, fetch_trace

OUTPUT_FILE = os.path.join("results", "step12_output.txt")

# Соседние станции и их вертикальный канал (как нашёл step11).
NEIGHBORS = {
    "JP.JGF": "BHZ",
    "JP.JSD": "BHZ",
    "G.INU": "BHZ",
    "PS.TSK": "HHZ",
}
NEIGHBOR_THRESHOLDS = (5, 7, 10)  # пороги, которые пробуем для соседей

# Время очагов (UTC) скопировано из step.7.py: тот файл нельзя импортировать
# (он выполняется целиком при импорте, и в имени есть точка).
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
BEFORE, AFTER = 15 * 60, 10 * 60  # окно вокруг очага: -15 ... +10 минут
QUIET_SEC = 3600                  # тихий час длится ровно час
EQ_DETECT_SEC = 120               # MAJO "заметила" землетрясение (как в step7)
COVER_TOL = 2                     # допуск при проверке покрытия записью, сек


def majo_window(name, start, end):
    """Запись MAJO из файла, который уже лежит в репозитории (как в step7)."""
    st = read(f"{name}.mseed")
    st.merge(fill_value=0)
    tr = st[0]
    tr.trim(start, end)
    return tr


def neighbor_trace(client, key, start, end):
    """Запись соседа: сначала нужный канал, потом второй (BHZ/HHZ).

    Возвращает (трасса, None) или (None, причина). Не падает.
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
    """Тревоги по готовой кривой STA/LTA при заданном пороге (порог выкл. = thr/2)."""
    fs = tr.stats.sampling_rate
    alarms = []
    for on, _off in trigger_onset(ratio, thr, thr / 2):
        t = tr.stats.starttime + on / fs
        if not alarms or t - alarms[-1] > GROUP_SEC:
            alarms.append(t)
    return alarms


def covers(tr, alarm):
    """Покрывает ли запись весь интервал +-60 сек вокруг тревоги."""
    return (tr.stats.starttime <= alarm - MATCH_SEC + COVER_TOL
            and tr.stats.endtime >= alarm + MATCH_SEC - COVER_TOL)


def process(report, client, label, majo_tr, start, end):
    """Один отрезок (землетрясение или тихий час): MAJO + 4 соседа.

    Возвращает словарь: тревоги MAJO и по каждому соседу кривую STA/LTA.
    """
    _, _, majo_alarms = analyze(majo_tr)  # порог 10, как в step11
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
    """Статус тревоги MAJO при пороге соседей thr.

    "подтверждена"  - хотя бы одна соседняя станция сработала в +-60 с;
    "отсеяна"       - данные соседей были, но никто не сработал;
    "нет данных"    - ни одна соседняя станция не покрывает окно +-60 с.
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

    # --- 1. Землетрясения ---
    report.line("=== Землетрясения (запись от -15 до +10 минут вокруг очага) ===")
    quakes = []  # (дата, данные, тревога MAJO, замечающая землетрясение или None)
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
        # Тревога, которой MAJO "заметила" землетрясение: первая в 0..+120 с
        # после очага (то же правило, что в step7).
        hits = [t for t in data["majo"] if origin <= t <= origin + EQ_DETECT_SEC]
        quakes.append((text, data, hits[0] if hits else None))
        if hits:
            report.line(f"  MAJO заметила землетрясение через "
                        f"{round(hits[0] - origin)} с")
        else:
            report.line("  MAJO НЕ заметила землетрясение (порог 10)")
    report.line()

    # --- 2. Тихие часы ---
    report.line("=== Тихие часы (по часу) ===")
    quiet = []  # (час, данные)
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

    # --- 3. Таблицы по порогам ---
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
