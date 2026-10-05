"""Шаг 14: прогон 10 землетрясений и 24 тихих часов через сервер.

Запуск из корня проекта (Windows):
    .venv\\Scripts\\python -m server.evaluate
Linux/macOS:
    .venv/bin/python -m server.evaluate

Каждый сценарий (землетрясение или тихий час) для каждого значения
QUAKE_MIN_STATIONS (2 и 3) идёт через сервер (TestClient) со своей временной
базой. Тихие часы берём из results/step13_output.txt (те самые 24 часа, не
новые). Потом сравниваем с пакетными цифрами step13 (R1 и R2) и ищем причины
расхождений. Вывод: results/step14_output.txt. Файлы step1-13 и параметры
детектора не трогаем; записи нужны в data/ (их скачал step13.py).
"""

import contextlib
import io
import os
import re
import tempfile
from concurrent.futures import ProcessPoolExecutor

from fastapi.testclient import TestClient
from obspy import UTCDateTime, read

from server import replay
from server.app import create_app
from server.detector import WARMUP_SEC
from step11 import MATCH_SEC, Report, analyze
from step12 import AFTER, BEFORE, NEIGHBORS, ORIGINS, alarms_at, majo_window
from step13 import HOUR_SEC, cache_path, judge

OUTPUT_FILE = os.path.join("results", "step14_output.txt")
STEP13_FILE = os.path.join("results", "step13_output.txt")
MAJO = "IU.MAJO"
MIN_STATIONS_VALUES = (2, 3)
EQ_WINDOW = 120   # "замечено": тревога в 0..+120 с после очага (как в step7)
MATCH_TOL = 3     # тревоги потока и пакета считаем одной, если разница <= 3 с
CHUNK_SEC = 10    # длина куска при воспроизведении


# ---------------------------------------------------------------------------
# Входные данные: часы и цифры step13 берём из его вывода, ничего не придумываем.
# ---------------------------------------------------------------------------
def quiet_hours_from_step13(path):
    """24 принятых тихих часа из вывода step13 (вид '2016-06-11T17')."""
    hours, candidate = [], None
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^(\d{4}-\d\d-\d\dT\d\d):00: тихий по каталогу", line)
            if m:
                candidate = m.group(1)
            elif line.startswith("  принят час №") and candidate:
                hours.append(candidate)
                candidate = None
            elif re.match(r"^\d{4}-\d\d-\d\dT\d\d:00:", line):
                candidate = None  # "не тихий" или пропущенный час
    return hours


def step13_table(path):
    """Строки R0-R3 из итоговой таблицы step13."""
    rows = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\s+(R\d)\s+(\d+) из (\d+)\s+(\d+)\s+(\d+)\s+(\d+)"
                         r"\s+([\d.]+)\s*$", line)
            if m:
                rows[m.group(1)] = dict(
                    found=int(m.group(2)), total=int(m.group(3)),
                    q_nodata=int(m.group(4)), false=int(m.group(5)),
                    h_nodata=int(m.group(6)), rate=float(m.group(7)))
    return rows


# ---------------------------------------------------------------------------
# Работа в отдельных процессах: прогон через сервер и пакетная сверка.
# ---------------------------------------------------------------------------
def run_server(scenario, min_stations):
    """Один сценарий через сервер со своей временной базой."""
    notes = io.StringIO()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        app = create_app(os.path.join(tmp, "t.db"), min_stations)
        client = TestClient(app)
        with contextlib.redirect_stdout(notes):
            replay.run(scenario, client, CHUNK_SEC, 0, verbose=False)
        alarms = client.get("/api/alarms", params=dict(limit=1000000)).json()
        events = client.get("/api/events", params=dict(limit=1000000)).json()
        app.state.db.conn.close()
    for a in alarms:
        a["ts"] = UTCDateTime(a["time"]).timestamp
    for e in events:
        for a in e["alarms"]:
            a["ts"] = UTCDateTime(a["time"]).timestamp
    return dict(scenario=scenario, n=min_stations, alarms=alarms,
                events=events, notes=notes.getvalue().strip())


def reference(scenario):
    """Пакетные тревоги (как в step12/13) для сверки с потоком."""
    kind, _, arg = scenario.partition(":")
    if kind == "quake":
        origin = UTCDateTime(next(o for o in ORIGINS if o.startswith(arg)))
        majo = majo_window("ev_" + arg, origin - BEFORE, origin + AFTER)
        tag = "quake_" + arg
    else:
        t0 = UTCDateTime(arg + ":00:00")
        tag = t0.strftime("hour_%Y%m%dT%H")
        majo = read(cache_path(MAJO, tag))[0].copy()
        majo.trim(t0, t0 + HOUR_SEC)
    _, _, majo_alarms = analyze(majo)
    stations, neighbors, starts = {}, {}, {MAJO: majo.stats.starttime.timestamp}
    for key in NEIGHBORS:
        path = cache_path(key, tag)
        if not os.path.exists(path):
            continue
        tr = read(path)[0]
        tr_f, ratio, _ = analyze(tr)
        alarms = {thr: alarms_at(tr_f, ratio, thr) for thr in (7, 10)}
        stations[key] = (tr_f, ratio, alarms)
        neighbors[key] = [t.timestamp for t in alarms[10]]
        starts[key] = tr.stats.starttime.timestamp
    return dict(
        scenario=scenario, starts=starts, neighbors=neighbors,
        majo=[t.timestamp for t in majo_alarms],
        # правила step13 для каждой тревоги MAJO: R1 (>=1 сосед), R2 (>=2)
        r1=[judge(stations, t, 1, 10) for t in majo_alarms],
        r2=[judge(stations, t, 2, 10) for t in majo_alarms])


def _task(args):
    kind, scenario, n = args
    return (kind, scenario, n,
            reference(scenario) if kind == "ref" else run_server(scenario, n))


# ---------------------------------------------------------------------------
# Подсчёт показателей.
# ---------------------------------------------------------------------------
def confirmed_ids(res):
    return {e["id"] for e in res["events"] if e["status"] == "confirmed"}


def quake_stats(res, origin_ts):
    """Показатели одного землетрясения для одного значения N."""
    ok_ids = confirmed_ids(res)
    in_win = lambda a: origin_ts <= a["ts"] <= origin_ts + EQ_WINDOW
    events = [e for e in res["events"] if e["id"] in ok_ids
              and any(in_win(a) for a in e["alarms"])]
    with_majo = [e for e in events
                 if any(a["station"] == MAJO for a in e["alarms"])]
    majo_win = sorted((a for a in res["alarms"]
                       if a["station"] == MAJO and in_win(a)),
                      key=lambda a: a["ts"])
    return dict(
        event=bool(events), with_majo=bool(with_majo), events=events,
        majo_alarm=majo_win[0] if majo_win else None,
        majo_in_event=bool(majo_win and majo_win[0]["event_id"] in ok_ids))


def hour_stats(res):
    """Показатели одного тихого часа для одного значения N."""
    ok_ids = confirmed_ids(res)
    events = [e for e in res["events"] if e["id"] in ok_ids]
    no_majo = [e for e in events
               if not any(a["station"] == MAJO for a in e["alarms"])]
    majo = [a for a in res["alarms"] if a["station"] == MAJO]
    return dict(events=len(events), no_majo=len(no_majo), majo=len(majo),
                majo_in_event=sum(1 for a in majo if a["event_id"] in ok_ids))


def match_times(batch, stream):
    """Жадное сопоставление времён (одно к одному, допуск MATCH_TOL)."""
    free, pairs, only_batch = sorted(stream), [], []
    for b in sorted(batch):
        hit = next((s for s in free if abs(s - b) <= MATCH_TOL), None)
        if hit is None:
            only_batch.append(b)
        else:
            free.remove(hit)
            pairs.append((b, hit))
    return pairs, only_batch, free


def stamp(ts):
    return str(UTCDateTime(ts))[:19]


# ---------------------------------------------------------------------------
def main():
    hours = quiet_hours_from_step13(STEP13_FILE)
    table13 = step13_table(STEP13_FILE)
    assert len(hours) == 24, f"в step13 найдено {len(hours)} часов, а не 24"
    quakes = [f"quake:{o[:10]}" for o in ORIGINS]
    scenarios = quakes + [f"hour:{h}" for h in hours]
    origin_ts = {f"quake:{o[:10]}": UTCDateTime(o).timestamp for o in ORIGINS}

    tasks = [("ref", s, 0) for s in scenarios] + [
        ("srv", s, n) for n in MIN_STATIONS_VALUES for s in scenarios]
    with ProcessPoolExecutor(max_workers=min(4, os.cpu_count() or 1)) as pool:
        done = list(pool.map(_task, tasks))
    ref = {s: r for kind, s, n, r in done if kind == "ref"}
    srv = {(s, n): r for kind, s, n, r in done if kind == "srv"}

    out = Report(OUTPUT_FILE)
    L = out.line
    L("Шаг 14: 10 землетрясений и 24 тихих часа (часы из results/step13_output.txt)"
      " через сервер, QUAKE_MIN_STATIONS = " + ", ".join(
          map(str, MIN_STATIONS_VALUES)))
    L("Детектор везде один (порог 10, как в step11); правило сети: тревоги "
      f"разных станций в пределах +-{MATCH_SEC} с = событие; событие "
      "'подтверждено', если в нём не меньше N разных станций.")
    L(f"Тихих часов: {len(hours)}. Каждый прогон: своя временная база.")
    L()

    stats_q, stats_h = {}, {}
    for n in MIN_STATIONS_VALUES:
        L(f"################ QUAKE_MIN_STATIONS = {n} ################")
        L()
        L("--- Землетрясения ---")
        L("  ('событие' = подтверждённое событие с тревогой в 0..+120 с после очага)")
        for s in quakes:
            st = quake_stats(srv[(s, n)], origin_ts[s])
            stats_q[(s, n)] = st
            line = f"  {s[6:]}: "
            if st["event"]:
                ev = st["events"][0]
                first = min(a["ts"] for a in ev["alarms"]) - origin_ts[s]
                names = ", ".join(sorted({a["station"] for a in ev["alarms"]}))
                line += (f"событие есть (станций {ev['n_stations']}: {names}; "
                         f"первая тревога через {first:.0f} с); тревога MAJO в "
                         f"событии: {'да' if st['with_majo'] else 'НЕТ'}")
            else:
                alarms = [f"{a['station']} {a['ts'] - origin_ts[s]:+.0f} с"
                          for a in srv[(s, n)]["alarms"]
                          if origin_ts[s] <= a["ts"] <= origin_ts[s] + EQ_WINDOW]
                line += ("подтверждённого события НЕТ; тревоги в окне: "
                         + (", ".join(alarms) or "нет"))
            L(line)
        n_ev = sum(stats_q[(s, n)]["event"] for s in quakes)
        n_mj = sum(stats_q[(s, n)]["with_majo"] for s in quakes)
        L(f"  Итого: подтверждённое событие у {n_ev} из {len(quakes)}; "
          f"из них с тревогой MAJO: {n_mj}")
        L()
        L("--- Тихие часы ---")
        for h in hours:
            st = hour_stats(srv[(f"hour:{h}", n)])
            stats_h[(h, n)] = st
            L(f"  {h}:00  тревог MAJO {st['majo']}, подтверждённых событий "
              f"{st['events']} (без тревоги MAJO: {st['no_majo']}), "
              f"тревог MAJO внутри событий: {st['majo_in_event']}")
        tot_ev = sum(stats_h[(h, n)]["events"] for h in hours)
        tot_no = sum(stats_h[(h, n)]["no_majo"] for h in hours)
        L(f"  Итого: подтверждённых событий {tot_ev} за {len(hours)} ч "
          f"= {tot_ev / len(hours):.2f} в час; из них без тревоги MAJO: {tot_no}")
        L()

    # --- Общая таблица ---
    L("################ СВОДНАЯ ТАБЛИЦА ################")
    L()
    L("А) Метрики сети (как просили): события, а не отдельные тревоги MAJO")
    head = ("  " + "N".ljust(4) + "землетр. с подтв. событием".ljust(30)
            + "из них с MAJO".ljust(16) + "подтв. событий в тихих".ljust(24)
            + "в час".ljust(8) + "без MAJO")
    L(head)
    for n in MIN_STATIONS_VALUES:
        n_ev = sum(stats_q[(s, n)]["event"] for s in quakes)
        n_mj = sum(stats_q[(s, n)]["with_majo"] for s in quakes)
        tot = sum(stats_h[(h, n)]["events"] for h in hours)
        no = sum(stats_h[(h, n)]["no_majo"] for h in hours)
        L("  " + str(n).ljust(4) + f"{n_ev} из {len(quakes)}".ljust(30)
          + f"{n_mj}".ljust(16) + f"{tot}".ljust(24)
          + f"{tot / len(hours):.2f}".ljust(8) + f"{no}")
    L()
    L("Б) Сравнение с step13 по тревогам MAJO (то, что мерил step13): "
      "землетрясение 'замечено', если первая тревога MAJO в 0..+120 с после "
      "очага подтверждена; 'ложная' = тревога MAJO в тихом часе осталась "
      "подтверждённой")
    L("  " + "правило".ljust(30) + "замечено из 10".ljust(18)
      + "ложных MAJO-тревог".ljust(22) + "из всех".ljust(10) + "в час")
    pairs13 = {2: "R1", 3: "R2"}
    for n in MIN_STATIONS_VALUES:
        r = table13.get(pairs13[n])
        if r:
            L("  " + f"step13 {pairs13[n]} (MAJO + >={n - 1} сосед)".ljust(30)
              + f"{r['found']} из {r['total']}".ljust(18)
              + f"{r['false']}".ljust(22) + "18".ljust(10) + f"{r['rate']:.2f}")
        found = sum(stats_q[(s, n)]["majo_in_event"] for s in quakes)
        false = sum(stats_h[(h, n)]["majo_in_event"] for h in hours)
        total = sum(stats_h[(h, n)]["majo"] for h in hours)
        L("  " + f"сервер N={n} (поток)".ljust(30)
          + f"{found} из {len(quakes)}".ljust(18) + f"{false}".ljust(22)
          + f"{total}".ljust(10) + f"{false / len(hours):.2f}")
    L("  (у step13 в 'замечено' не входит землетрясение 2011 года для R2: "
      f"проверить правило было нельзя, 'нет данных': {table13['R2']['q_nodata']})")
    L()

    # --- Сверка потока с пакетом ---
    L("################ СВЕРКА ПОТОКА С ПАКЕТОМ (step12/13) ################")
    L("Тревоги потока (сервер) и пакета (analyze по всей записи) совпадают, "
      f"если разница <= {MATCH_TOL} с. Первые {WARMUP_SEC} с записи поток "
      "тревоги не выдаёт (прогрев).")
    L()
    totals = {}
    per_station_notes = []
    for s in scenarios:
        res = srv[(s, 2)]  # тревоги потока от N не зависят, берём N=2
        for key, start in ref[s]["starts"].items():
            batch = ref[s]["majo"] if key == MAJO else ref[s]["neighbors"][key]
            stream = [a["ts"] for a in res["alarms"] if a["station"] == key]
            pairs, only_b, only_s = match_times(batch, stream)
            warm = [t for t in only_b if t - start < WARMUP_SEC]
            t = totals.setdefault(key, dict(batch=0, stream=0, both=0,
                                            warm=0, other_b=0, only_s=0))
            t["batch"] += len(batch)
            t["stream"] += len(stream)
            t["both"] += len(pairs)
            t["warm"] += len(warm)
            t["other_b"] += len(only_b) - len(warm)
            t["only_s"] += len(only_s)
            for x in only_b:
                if x not in warm:
                    per_station_notes.append(
                        f"  {s} {key}: тревога пакета {stamp(x)} не найдена в потоке")
            for x in only_s:
                per_station_notes.append(
                    f"  {s} {key}: тревога потока {stamp(x)} не найдена в пакете")
    L("  " + "станция".ljust(10) + "пакет".ljust(8) + "поток".ljust(8)
      + "общих".ljust(8) + "только пакет: прогрев".ljust(24)
      + "только пакет: иначе".ljust(21) + "только поток")
    for key, t in sorted(totals.items()):
        L("  " + key.ljust(10) + str(t["batch"]).ljust(8)
          + str(t["stream"]).ljust(8) + str(t["both"]).ljust(8)
          + str(t["warm"]).ljust(24) + str(t["other_b"]).ljust(21)
          + str(t["only_s"]))
    for line in per_station_notes:
        L(line)
    L()

    # --- Расхождения по тревогам MAJO: поток против R1/R2 ---
    L("################ РАСХОЖДЕНИЯ ПО ТРЕВОГАМ MAJO ################")
    L("Для каждой тревоги MAJO пакета: решение step13 (R1 для N=2, R2 для N=3) "
      "против решения сервера (тревога MAJO внутри подтверждённого события).")
    L()
    causes = {}
    for n in MIN_STATIONS_VALUES:
        rule = pairs13[n]
        need = n - 1
        L(f"--- N={n} против {rule} ---")
        agree = diff = nodata = 0
        for s in scenarios:
            res, rf = srv[(s, n)], ref[s]
            ok_ids = confirmed_ids(res)
            stream_majo = [a for a in res["alarms"] if a["station"] == MAJO]
            for i, t in enumerate(rf["majo"]):
                verdict = (rf["r1"] if n == 2 else rf["r2"])[i]
                a = next((x for x in stream_majo
                          if abs(x["ts"] - t) <= MATCH_TOL), None)
                server_yes = a is not None and a["event_id"] in ok_ids
                if verdict == "нет данных":
                    nodata += 1
                    L(f"  {s} {stamp(t)}: step13 {rule}='нет данных' "
                      f"(проверить было нельзя); сервер: "
                      f"{'подтверждена' if server_yes else 'не подтверждена'}")
                    continue
                if (verdict == "подтверждена") == server_yes:
                    agree += 1
                    continue
                diff += 1
                near_b = sorted(k for k, ts in rf["neighbors"].items()
                                if any(abs(x - t) <= MATCH_SEC for x in ts))
                near_s = sorted({x["station"] for x in res["alarms"]
                                 if x["station"] != MAJO
                                 and abs(x["ts"] - t) <= MATCH_SEC})
                if a is None:
                    why = ("тревоги MAJO нет в потоке: прогрев"
                           if t - rf["starts"][MAJO] < WARMUP_SEC
                           else "тревоги MAJO нет в потоке (причина не прогрев)")
                elif near_b != near_s:
                    why = "соседние тревоги потока и пакета различаются"
                elif server_yes and len(near_s) < need:
                    why = ("событие собралось цепочкой через другую станцию, "
                           f"прямых соседей в +-{MATCH_SEC} с меньше {need}")
                else:
                    why = "причина не найдена автоматически"
                causes[(n, why)] = causes.get((n, why), 0) + 1
                L(f"  {s} {stamp(t)}: step13 {rule}={verdict}, сервер="
                  f"{'подтверждена' if server_yes else 'не подтверждена'}; "
                  f"соседи в +-{MATCH_SEC} с: пакет {near_b or '-'}, "
                  f"поток {near_s or '-'}; причина: {why}")
        L(f"  Итого: совпало {agree}, не совпало {diff}, "
          f"step13 не мог проверить {nodata}")
        L()
    if causes:
        L("Причины расхождений (по числу тревог):")
        for (n, why), c in sorted(causes.items()):
            L(f"  N={n}: {c} шт.: {why}")
        L()
    notes = {r["notes"] for r in srv.values() if r["notes"]}
    if notes:
        L("Замечания воспроизведения (станции без файла data/):")
        for note in sorted(notes):
            L("  " + note.replace("\n", "\n  "))
        L()
    L(f"Вывод сохранён в {OUTPUT_FILE}")
    out.close()


if __name__ == "__main__":
    main()
