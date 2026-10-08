"""step 15: INDEPENDENT check on new earthquakes and new quiet hours.

All earlier numbers (steps 7-14) were obtained on events on which the
settings were tuned. Here we take events we have not seen before and do not
fit anything: the definitions below are written down before the run and do not
change, the network rule (N=3, server/network.py), the detector (step11) and
the thresholds stay the same. If the result is bad, it is recorded as it is.

What the script does:
  1) picks up to 60 new earthquakes M4.5-5.7 from the USGS catalog (20 per
     magnitude bin) and 150 new quiet hours (random.seed(7));
  2) downloads the records of MAJO and 4 neighbor stations into data/ (it can be
     interrupted and started again: what is downloaded is not downloaded twice);
  3) runs every scenario through the server (TestClient, rule N=3);
  4) counts the shares of detected and confirmed earthquakes with Wilson
     intervals and the false alarms per hour; draws results/step15_detection.png.

Run (Windows):  .venv\\Scripts\\python step15.py
Output: results/step15_output.txt
"""

import bisect
import json
import math
import os
import random
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

import numpy as np
from fastapi.testclient import TestClient
from obspy import UTCDateTime, read
from obspy.clients.fdsn import Client
from obspy.clients.fdsn.header import FDSNNoDataException
from obspy.geodetics import gps2dist_azimuth

from server import replay
from server.app import create_app
from server.evaluate import quiet_hours_from_step13
from step11 import MATCH_SEC, Report, fetch_trace
from step12 import AFTER, BEFORE, NEIGHBORS, ORIGINS, neighbor_trace
from step13 import (BOX, HOUR_SEC, MAJO_ID, MIN_MAJO_SEC, MIN_NEIGHBORS,
                    cache_path, random_hours)

OUTPUT_FILE = os.path.join("results", "step15_output.txt")
PLOT_FILE = os.path.join("results", "step15_detection.png")
STEP13_FILE = os.path.join("results", "step13_output.txt")
DATA_DIR = "data"
MAJO = "IU.MAJO"
MAJO_LAT, MAJO_LON = 36.54567, 138.20406   # as in server/stations.json

# definitions: written at the start of results/step15_output.txt BEFORE any downloads
DEFINITIONS = """\
ОПРЕДЕЛЕНИЯ (записаны до запуска, после запуска не меняются)
  1. "Замечено": у MAJO есть тревога в пределах 0..+150 с после времени очага.
  2. "Подтверждено сетью": первая такая тревога MAJO входит в подтверждённое
     событие сети. Правило N=3 как в server/network.py: тревоги не менее чем
     3 станций из 5 (IU.MAJO, JP.JGF, JP.JSD, G.INU, PS.TSK) в пределах 60 с.
  3. Ложное срабатывание: подтверждённое событие сети в тихий час (для одной
     станции: тревога MAJO в тихий час).
ЗАРАНЕЕ ЗАФИКСИРОВАННЫЕ УСЛОВИЯ
  * Землетрясения: каталог USGS, рамка 30-45 с.ш., 130-146 в.д., 2016-2024,
    M4.5-5.7, без 10 событий step13 (+-1 час). Исключены: события, у которых
    в +-10 минут от окна записи (окно: -15..+10 мин вокруг очага) есть другое
    M4+ в рамке, и события в первые 30 минут после землетрясения M5.5+ в рамке.
    До 20 событий на каждый интервал магнитуды (4.5-4.9, 5.0-5.3, 5.4-5.7),
    random.seed(7); событие без записи IU.MAJO.00.BHZ заменяется следующим.
  * Тихие часы: 150 новых часов 2016-2024 (random.seed(7)), не из 24 часов
    step13; проверки по каталогу как в step13 (нет M4+ в рамке в окне
    -10..+70 мин от начала часа и нет M5.5+ в мире за 30 мин до часа); как в
    step13 нужна запись MAJO (00.BHZ) и данные не менее чем двух соседей.
  * Детектор и правило не менялись: фильтр 1-8 Гц, STA 1 с / LTA 30 с, порог 10
    на всех станциях; потоковый детектор сервера первые 60 с записи молчит.
  * Доли: доверительный интервал Уилсона 95%; ложные срабатывания в час:
    точный пуассоновский интервал 95% и бутстрап по часам (seed 7).
"""

# magnitude and distance bins (the borders are set in advance)
MAG_BINS = [("4.5-4.9", 4.5, 5.0), ("5.0-5.3", 5.0, 5.4), ("5.4-5.7", 5.4, 5.8)]
DIST_BINS = [("<150 км", 0, 150), ("150-300 км", 150, 300), (">300 км", 300, 1e9)]
PER_BIN = 20            # events per magnitude bin
N_HOURS = 150
SEED = 7
EQ_WINDOW = 150         # "detected": 0..+150 s after the origin
MIN_EQ_MAJO_SEC = 0.98 * (BEFORE + AFTER)   # MAJO record for an earthquake: >= 98% of the window
MIN_NET = 3             # network rule (N=3)
CHUNK_SEC = 10
WORKERS_DOWNLOAD = 6
WORKERS_RUN = 4
RUN_VERSION = "1"       # version of the run-results cache (data/step15_runs)


# statistics
def wilson(k, n, z=1.96):
    """Wilson confidence interval (95%) for the share k out of n"""
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def _poisson_cdf(k, mu):
    # via logarithms: for large k, mu**i and i! do not fit into a float
    if mu <= 0:
        return 1.0
    return min(1.0, sum(math.exp(-mu + i * math.log(mu) - math.lgamma(i + 1))
                        for i in range(k + 1)))


def poisson_ci(k, alpha=0.05):
    """exact (Garwood) interval for the mean number of events given k observations"""
    def bisect_mu(f, lo, hi):
        for _ in range(200):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if f(mid) > 0 else (lo, mid)
        return (lo + hi) / 2
    top = k + 10 * math.sqrt(k + 1) + 20
    lower = 0.0 if k == 0 else bisect_mu(
        lambda mu: alpha / 2 - (1 - _poisson_cdf(k - 1, mu)), 0.0, top)
    upper = bisect_mu(lambda mu: _poisson_cdf(k, mu) - alpha / 2, 0.0, top)
    return lower, upper


def bootstrap_rate_ci(counts, reps=10000):
    """interval for the number of alarms per hour: we resample whole HOURS"""
    rng = np.random.default_rng(SEED)
    counts = np.asarray(counts, dtype=float)
    means = rng.choice(counts, size=(reps, len(counts))).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


# USGS catalog (cached in data/ so a second run does not query the catalog)
def load_catalog(name, **query):
    """catalog events by year 2016-2024: a list of dicts (time, M, place)"""
    path = os.path.join(DATA_DIR, f"step15_catalog_{name}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    client = Client("USGS", timeout=180)
    events = []
    for year in range(2016, 2025):
        try:
            cat = client.get_events(
                starttime=UTCDateTime(year, 1, 1),
                endtime=UTCDateTime(year + 1, 1, 1), orderby="time-asc", **query)
        except FDSNNoDataException:
            continue
        for ev in cat:
            origin = ev.preferred_origin() or ev.origins[0]
            mag = ev.preferred_magnitude() or (ev.magnitudes or [None])[0]
            if mag is None or mag.mag is None:
                continue
            events.append(dict(
                time=origin.time.timestamp, mag=float(mag.mag),
                lat=origin.latitude, lon=origin.longitude,
                depth=None if origin.depth is None else origin.depth / 1000,
                type=str(ev.event_type) if ev.event_type else "earthquake"))
    events.sort(key=lambda e: e["time"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(events, f)
    return events


class QuietChecker:
    """the same checks as step13.catalog_is_quiet, but on the catalogs already downloaded"""

    def __init__(self, regional, world):
        self.reg = [e["time"] for e in regional]   # M4+ in the box, sorted
        self.world = [e["time"] for e in world]    # M5.5+ in the world, sorted

    @staticmethod
    def _any(times, lo, hi):
        i = bisect.bisect_left(times, lo)
        return i < len(times) and times[i] <= hi

    def is_quiet(self, t0):
        ts = t0.timestamp
        return not (self._any(self.reg, ts - 600, ts + HOUR_SEC + 600)
                    or self._any(self.world, ts - 1800, ts))


# downloading records: resumable, thread-safe
class DownloadError(RuntimeError):
    """network error: abort the run so as not to exclude an event by mistake"""


_local = threading.local()


def waves_client():
    if not hasattr(_local, "client"):
        _local.client = Client("EARTHSCOPE", timeout=120)
    return _local.client


def get_record(key, start, end, tag):
    """(trace, None) or (None, reason). The "no data" result is cached too"""
    path = cache_path(key, tag)
    if os.path.exists(path):
        return read(path)[0], None
    if os.path.exists(path + ".nodata"):
        return None, "нет данных (кэш)"
    note = ""
    for attempt in range(3):
        if key == MAJO:
            tr, note = fetch_trace(waves_client(), MAJO, "BHZ", start, end)
            if tr is not None and tr.id != MAJO_ID:
                tr, note = None, f"нет записи 00.BHZ (нашлась {tr.id})"
        else:
            tr, note = neighbor_trace(waves_client(), key, start, end)
        if tr is not None:
            tr.write(path, format="MSEED")
            return tr, None
        if "ОШИБКА" not in note:
            open(path + ".nodata", "w").close()   # the server answered "no data"
            return None, note
        time.sleep(5 * (attempt + 1))
    raise DownloadError(f"{key} {tag}: {note}")


def fetch_many(jobs):
    """jobs: a list of (key, start, end, tag); returns {(key, tag): (trace, reason)}"""
    with ThreadPoolExecutor(max_workers=WORKERS_DOWNLOAD) as pool:
        futures = {(k, tag): pool.submit(get_record, k, s, e, tag)
                   for k, s, e, tag in jobs}
        return {key: f.result() for key, f in futures.items()}


def majo_ok(tr, need_sec):
    return tr is not None and tr.stats.npts / tr.stats.sampling_rate >= need_sec


# choosing earthquakes
def eligible_events(regional, report):
    """candidates by the conditions set in advance. Prints the selection funnel"""
    times = [e["time"] for e in regional]
    big = [e["time"] for e in regional if e["mag"] >= 5.5]
    known = [UTCDateTime(o).timestamp for o in ORIGINS]
    funnel = dict(all=0, window=0, step13=0, other_m4=0, aftershock=0, ok=0)
    out = []
    for e in regional:
        funnel["all"] += 1
        if not (4.5 <= e["mag"] <= 5.7) or e["type"] != "earthquake":
            continue
        funnel["window"] += 1
        t = e["time"]
        if any(abs(t - k) < 3600 for k in known):
            funnel["step13"] += 1
            continue
        # another M4+ in the record window +-10 minutes: [t-900-600, t+600+600]; minus the event itself
        near = (bisect.bisect_right(times, t + AFTER + 600)
                - bisect.bisect_left(times, t - BEFORE - 600)) - 1
        if near > 0:
            funnel["other_m4"] += 1
            continue
        i = bisect.bisect_left(big, t - 1800)
        if any(0 < t - b <= 1800 for b in big[i:i + 50]):
            funnel["aftershock"] += 1
            continue
        funnel["ok"] += 1
        out.append(e)
    report.line("Воронка отбора землетрясений: в каталоге M4+ в рамке "
                f"{funnel['all']}; M4.5-5.7 (тип earthquake): {funnel['window']}; "
                f"рядом с событиями step13: {funnel['step13']}; "
                f"другое M4+ в +-10 мин от окна: {funnel['other_m4']}; "
                f"в 30 мин после M5.5+: {funnel['aftershock']}; "
                f"остаётся кандидатов: {funnel['ok']}")
    return out


def quake_tag(e):
    return UTCDateTime(e["time"]).strftime("s15q_%Y%m%dT%H%M%S")


def pick_events(candidates, report):
    """up to PER_BIN events per magnitude bin; no MAJO record: take the next one"""
    random.seed(SEED)
    chosen = []
    for name, lo, hi in MAG_BINS:
        pool = [e for e in candidates if lo <= e["mag"] < hi]
        random.shuffle(pool)
        accepted, pos, no_majo = [], 0, 0
        while len(accepted) < PER_BIN and pos < len(pool):
            batch = pool[pos:pos + (PER_BIN - len(accepted))]
            pos += len(batch)
            jobs = [(MAJO, UTCDateTime(e["time"]) - BEFORE,
                     UTCDateTime(e["time"]) + AFTER, quake_tag(e)) for e in batch]
            got = fetch_many(jobs)
            for e, job in zip(batch, jobs):
                tr, _ = got[(MAJO, job[3])]
                if majo_ok(tr, MIN_EQ_MAJO_SEC):
                    accepted.append(e)
                else:
                    no_majo += 1
        report.line(f"  M{name}: кандидатов {len(pool)}, взято {len(accepted)}, "
                    f"пропущено из-за записи MAJO: {no_majo}")
        chosen += accepted
    return chosen


# choosing quiet hours
def pick_hours(checker, report):
    random.seed(SEED)
    used13 = set(quiet_hours_from_step13(STEP13_FILE))
    stats = dict(tried=0, in13=0, noisy=0, majo=0, neighbors=0)
    accepted = []
    gen = random_hours(100000)
    while len(accepted) < N_HOURS:
        batch = []
        while len(batch) < 40:
            t0 = next(gen)
            stats["tried"] += 1
            if str(t0)[:13] in used13:
                stats["in13"] += 1
            elif not checker.is_quiet(t0):
                stats["noisy"] += 1
            else:
                batch.append(t0)
        tag = lambda t0: t0.strftime("s15h_%Y%m%dT%H")
        majo = fetch_many([(MAJO, t0, t0 + HOUR_SEC, tag(t0)) for t0 in batch])
        with_majo = [t0 for t0 in batch
                     if majo_ok(majo[(MAJO, tag(t0))][0], MIN_MAJO_SEC)]
        stats["majo"] += len(batch) - len(with_majo)
        nb = fetch_many([(k, t0, t0 + HOUR_SEC, tag(t0))
                         for t0 in with_majo for k in NEIGHBORS])
        for t0 in with_majo:   # accept in the order of selection until we have 150
            if len(accepted) >= N_HOURS:
                break
            have = [k for k in NEIGHBORS if nb[(k, tag(t0))][0] is not None]
            if len(have) < MIN_NEIGHBORS:
                stats["neighbors"] += 1
            else:
                accepted.append(t0)
    report.line(f"Тихие часы: перебрано кандидатов {stats['tried']}; из step13: "
                f"{stats['in13']}; не тихие по каталогу: {stats['noisy']}; нет "
                f"записи MAJO: {stats['majo']}; меньше {MIN_NEIGHBORS} соседей с "
                f"данными: {stats['neighbors']}; принято {len(accepted)}")
    return accepted


# running through the server (in separate processes)
def run_job(job):
    """one scenario through the server with rule N=3. job = (tag, {station: file})"""
    tag, files = job
    cache = os.path.join(DATA_DIR, "step15_runs", f"{RUN_VERSION}_{tag}.json")
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    traces = {code: read(path)[0] for code, path in files.items()}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        app = create_app(os.path.join(tmp, "t.db"), MIN_NET)
        client = TestClient(app)
        for _end, code, payload in replay.make_chunks(traces, CHUNK_SEC):
            client.post(f"/api/ingest/{code}", json=payload).raise_for_status()
        alarms = client.get("/api/alarms", params=dict(limit=1000000)).json()
        events = client.get("/api/events", params=dict(limit=1000000)).json()
        app.state.db.conn.close()
    res = dict(
        tag=tag, stations=sorted(traces),
        alarms=[dict(station=a["station"], ts=UTCDateTime(a["time"]).timestamp,
                     event_id=a["event_id"]) for a in alarms],
        events=[dict(id=e["id"], status=e["status"], n=e["n_stations"],
                     stations=sorted({a["station"] for a in e["alarms"]}),
                     has_majo=any(a["station"] == MAJO for a in e["alarms"]))
                for e in events])
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(res, f)
    return res


def files_for(tag):
    """station files of the scenario that were really downloaded (with data)"""
    files = {}
    for key in [MAJO] + list(NEIGHBORS):
        path = cache_path(key, tag)
        if os.path.exists(path):
            files[key] = path
    return files


# evaluating the results
def judge_quake(e, res):
    """numbers of one earthquake (definitions 1 and 2)"""
    t = e["time"]
    ok = {ev["id"] for ev in res["events"] if ev["status"] == "confirmed"}
    in_win = sorted((a for a in res["alarms"]
                     if a["station"] == MAJO and t <= a["ts"] <= t + EQ_WINDOW),
                    key=lambda a: a["ts"])
    seen = bool(in_win)
    net = seen and in_win[0]["event_id"] in ok
    stations = sorted({a["station"] for a in res["alarms"]
                       if t <= a["ts"] <= t + EQ_WINDOW})
    return dict(seen=seen, net=bool(net), delay=in_win[0]["ts"] - t if seen else None,
                stations=stations, n_data=len(res["stations"]))


def table_rows(items, bins, key):
    """table rows: (label, n, detected, network) per bin"""
    rows = []
    for name, lo, hi in bins:
        sel = [i for i in items if lo <= key(i) < hi]
        rows.append((name, len(sel), sum(i["seen"] for i in sel),
                     sum(i["net"] for i in sel)))
    return rows


def fmt_share(k, n):
    if n == 0:
        return "нет событий"
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {100 * k / n:.0f}%  [{100 * lo:.0f}-{100 * hi:.0f}%]"


def plot_detection(items, path):
    """plot: share detected against magnitude (Wilson intervals 95%)"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 5))
    rng = np.random.default_rng(SEED)
    for off, field, color, label in ((-0.03, "seen", "#1c7ed6", "MAJO alone"),
                                     (0.03, "net", "#e8590c", "Network (3 of 5)")):
        ax.scatter([i["mag"] + rng.uniform(-0.04, 0.04) for i in items],
                   [i[field] + rng.uniform(-0.03, 0.03) + off for i in items],
                   s=12, alpha=0.25, color=color)
        xs, ys, lo_err, hi_err = [], [], [], []
        for name, lo, hi in MAG_BINS:
            sel = [i for i in items if lo <= i["mag"] < hi]
            if not sel:
                continue
            k = sum(i[field] for i in sel)
            a, b = wilson(k, len(sel))
            xs.append(sum(i["mag"] for i in sel) / len(sel) + off)
            ys.append(k / len(sel))
            lo_err.append(k / len(sel) - a)
            hi_err.append(b - k / len(sel))
        ax.errorbar(xs, ys, yerr=[lo_err, hi_err], fmt="o-", color=color,
                    capsize=4, lw=2, label=label)
    ax.set_xlabel("Magnitude (USGS)")
    ax.set_ylabel("Fraction detected (0..+150 s after origin)")
    ax.set_title("Detection of new earthquakes (not used for tuning)\n"
                 "bars: Wilson 95% interval; dots: individual events")
    ax.set_ylim(-0.1, 1.1)
    ax.grid(alpha=0.3)
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    out = Report(OUTPUT_FILE)
    L = out.line
    for line in DEFINITIONS.rstrip("\n").split("\n"):
        L(line)          # the definitions are written before any downloads
    L()
    try:
        run(out)
    except DownloadError as err:
        L()
        L(f"ПРЕРВАНО из-за сетевой ошибки: {err}")
        L("Запустите step15.py ещё раз: скачанное повторно не качается.")
        out.close()
        raise SystemExit(1)
    out.close()


def run(out):
    L = out.line
    L("=== Каталог USGS ===")
    regional = load_catalog("regional_m4", minmagnitude=4.0, **BOX)
    world = load_catalog("world_m55", minmagnitude=5.5)
    L(f"События каталога 2016-2024: M4+ в рамке {len(regional)}, "
      f"M5.5+ в мире {len(world)}")

    # earthquakes
    L()
    L("=== Выбор землетрясений ===")
    candidates = eligible_events(regional, out)
    events = pick_events(candidates, out)
    L(f"Землетрясений в выборке: {len(events)}")
    jobs = [(k, UTCDateTime(e["time"]) - BEFORE, UTCDateTime(e["time"]) + AFTER,
             quake_tag(e)) for e in events for k in NEIGHBORS]
    fetch_many(jobs)   # neighbors; no data at a station is not an error

    # quiet hours
    L()
    L("=== Выбор тихих часов ===")
    hours = pick_hours(QuietChecker(regional, world), out)

    # run through the server
    L()
    L("=== Прогон через сервер (правило N=3) ===")
    q_jobs = [(quake_tag(e), files_for(quake_tag(e)))
              for e in events]
    h_jobs = [(t0.strftime("s15h_%Y%m%dT%H"),
               files_for(t0.strftime("s15h_%Y%m%dT%H")))
              for t0 in hours]
    with ProcessPoolExecutor(max_workers=WORKERS_RUN) as pool:
        q_res = list(pool.map(run_job, q_jobs))
        h_res = list(pool.map(run_job, h_jobs))
    L(f"Прогнано сценариев: {len(q_res)} землетрясений и {len(h_res)} часов")

    # earthquakes: results
    L()
    L("=== Землетрясения: по каждому событию ===")
    items = []
    for e, res in zip(events, q_res):
        j = judge_quake(e, res)
        dist = gps2dist_azimuth(MAJO_LAT, MAJO_LON, e["lat"], e["lon"])[0] / 1000
        j.update(mag=e["mag"], depth=e["depth"], dist=dist, time=e["time"])
        items.append(j)
        delay = "нет" if j["delay"] is None else f"{j['delay']:+.0f} с"
        L(f"  {str(UTCDateTime(e['time']))[:19]}  M{e['mag']:.1f}  глубина "
          f"{'?' if e['depth'] is None else format(e['depth'], '.0f')} км  до MAJO "
          f"{dist:.0f} км  MAJO: {delay}  станций с тревогой в окне: "
          f"{len(j['stations'])} (данные есть у {j['n_data']})  "
          f"сеть: {'ДА' if j['net'] else 'нет'}")
    L()
    L("=== ИТОГ по землетрясениям (определения 1 и 2; интервал Уилсона 95%) ===")
    n = len(items)
    L(f"  Все:  замечено MAJO одной: {fmt_share(sum(i['seen'] for i in items), n)}")
    L(f"        подтверждено сетью:  {fmt_share(sum(i['net'] for i in items), n)}")
    for title, bins, key in (("по магнитуде", MAG_BINS, lambda i: i["mag"]),
                             ("по расстоянию до MAJO", DIST_BINS,
                              lambda i: i["dist"])):
        L()
        L(f"  {title}:")
        for name, size, seen, net in table_rows(items, bins, key):
            L(f"    {name:11} n={size:<3} MAJO: {fmt_share(seen, size):34} "
              f"сеть: {fmt_share(net, size)}")
    few = [i for i in items if i["n_data"] < MIN_NET]
    L()
    L(f"  Событий, где данных меньше чем у 3 станций (сеть подтвердить не могла "
      f"в принципе): {len(few)}")
    seen_not_net = [i for i in items if i["seen"] and not i["net"]]
    L(f"  Замечено MAJO, но не подтверждено сетью: {len(seen_not_net)}")
    for i in seen_not_net:
        L(f"    {str(UTCDateTime(i['time']))[:19]} M{i['mag']:.1f} "
          f"{i['dist']:.0f} км: станций с тревогой {len(i['stations'])} "
          f"({', '.join(i['stations']) or '-'}), данные у {i['n_data']}")
    missed = [i for i in items if not i["seen"]]
    L(f"  Не замечено MAJO вообще: {len(missed)}")
    for i in missed:
        L(f"    {str(UTCDateTime(i['time']))[:19]} M{i['mag']:.1f} "
          f"глубина {'?' if i['depth'] is None else format(i['depth'], '.0f')} км, "
          f"{i['dist']:.0f} км")
    plot_detection(items, PLOT_FILE)
    L(f"  График: {PLOT_FILE}")

    # quiet hours: results
    L()
    L("=== Тихие часы: по часам с тревогами ===")
    majo_counts, net_counts, no_majo = [], [], 0
    quiet_hours_zero = 0
    for t0, res in zip(hours, h_res):
        m = sum(1 for a in res["alarms"] if a["station"] == MAJO)
        conf = [ev for ev in res["events"] if ev["status"] == "confirmed"]
        no_majo += sum(1 for ev in conf if not ev["has_majo"])
        majo_counts.append(m)
        net_counts.append(len(conf))
        if m or conf:
            L(f"  {str(t0)[:13]}:00  тревог MAJO {m}, подтверждённых событий "
              f"{len(conf)} (без тревоги MAJO: "
              f"{sum(1 for ev in conf if not ev['has_majo'])}); станций с данными "
              f"{len(res['stations'])}")
        else:
            quiet_hours_zero += 1
    L(f"  Часов без единой тревоги и без событий: {quiet_hours_zero} из {len(hours)}")
    L()
    L("=== ИТОГ по тихим часам (определение 3) ===")
    nh = len(hours)
    for name, counts in (("одна станция (MAJO), тревог", majo_counts),
                         ("сеть N=3, подтверждённых событий", net_counts)):
        k = sum(counts)
        plo, phi = poisson_ci(k)
        blo, bhi = bootstrap_rate_ci(counts)
        wlo, whi = wilson(sum(1 for c in counts if c), nh)
        L(f"  {name}: {k} за {nh} ч = {k / nh:.3f} в час; пуассоновский "
          f"интервал [{plo / nh:.3f}-{phi / nh:.3f}]; бутстрап по часам "
          f"[{blo:.3f}-{bhi:.3f}]")
        L(f"      часов хотя бы с одним срабатыванием: "
          f"{fmt_share(sum(1 for c in counts if c), nh)}")
    L(f"  Подтверждённых событий без тревоги MAJO: {no_majo}")
    L()
    L("ПОЯСНЕНИЯ К ЧИСЛАМ")
    L("  * Срабатывания в часах группируются (несколько за раз), поэтому "
      "пуассоновский интервал слишком узкий; надёжнее бутстрап по часам.")
    L("  * Потоковый детектор первые 60 с каждой записи не выдаёт тревоги: "
      "доля часа 1/60 не проверялась, ложные срабатывания могут быть чуть занижены.")
    L("  * Часы и землетрясения без данных нужных станций исключались по "
      "правилам, записанным в начале файла; ни одно исключение не зависело "
      "от результата детектора.")
    L()
    L(f"Вывод сохранён в {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
