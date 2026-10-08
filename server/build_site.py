"""build the static site for GitHub Pages: the site/ folder.

The site needs no server: everything it shows is computed here in advance by the
same code as the real server (server/app.py, rule N=3) and saved as JSON.
In the browser these records are simply played back over time.

Run from the project root (Windows):
    .venv\\Scripts\\python -m server.build_site
Needs: records in data/ (downloaded by step13.py and step15.py), the results
results/step14_output.txt and results/step15_output.txt, and internet for the
USGS catalog (the answer is cached in data/site_meta.json).
"""

import json
import os
import re
import shutil
import tempfile

import numpy as np
from fastapi.testclient import TestClient
from obspy import UTCDateTime, read
from obspy.clients.fdsn import Client
from obspy.geodetics import gps2dist_azimuth

from server import replay
from server.app import create_app
from step11 import analyze
from step12 import AFTER, BEFORE, NEIGHBORS, majo_window
from step13 import cache_path
from step15 import wilson

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
DATA_OUT = os.path.join(SITE, "data")
META_CACHE = os.path.join(ROOT, "data", "site_meta.json")
STATIONS_FILE = os.path.join(ROOT, "server", "stations.json")
MAJO = "IU.MAJO"
MIN_NET = 3            # network rule, same as the server default
CHUNK_SEC = 10         # chunks, same as in replay
EQ_WINDOW = 150        # "detected": a MAJO alarm within 0..+150 s after the origin time (step 15)
ENV_STEP = 1.0         # signal envelope: min/max for every second
RATIO_CAP = 60.0       # STA/LTA above this is not needed on the chart

# scenarios for the site. The set is fixed here and not tuned to the results:
# two events from the tuning, good and BAD examples of the independent check
# (step 15) and quiet hours where the network both rejects alarms and is wrong itself
SCENARIOS = [
    dict(id="chiba-2021", kind="quake", group="tuning",
         origin="2021-10-07T13:41:24", tag="quake_2021-10-07",
         majo_file="ev_2021-10-07.mseed",
         note="Used while choosing the rule (step 14)."),
    dict(id="tohoku-2011", kind="quake", group="tuning",
         origin="2011-03-11T05:46:24", tag="quake_2011-03-11",
         majo_file="ev_2011-03-11.mseed",
         note="Used while choosing the rule. Only 3 of 5 stations have data, "
              "so the network rule is met with no margin."),
    dict(id="ind-2022-06-19", kind="quake", group="independent",
         origin="2022-06-19T06:08:06", tag="s15q_20220619T060806",
         note="Independent test (step 15): close event, confirmed."),
    dict(id="ind-2020-07-17", kind="quake", group="independent",
         origin="2020-07-17T04:49:34", tag="s15q_20200717T044934",
         note="Independent test: one of the smallest events (M4.5), confirmed."),
    dict(id="ind-2016-03-07", kind="quake", group="independent",
         origin="2016-03-07T02:40:25", tag="s15q_20160307T024025",
         note="Independent test, a FAILURE: MAJO alarmed, but only 2 stations "
              "did, so the network did not confirm it."),
    dict(id="ind-2018-08-03", kind="quake", group="independent",
         origin="2018-08-03T13:07:03", tag="s15q_20180803T130703",
         note="Independent test, a FAILURE: a deep event (about 490 km). "
              "MAJO gave no alarm within 150 s after the origin (its only alarm "
              "came 63 s before it), so by our definition it was missed."),
    dict(id="quiet-2019-06-14", kind="hour", group="quiet",
         start="2019-06-14T05", tag="s15h_20190614T05",
         note="Quiet hour (no M4+ in the study region in the USGS catalogue): "
              "MAJO alone raised several alarms, the network confirmed none."),
    dict(id="quiet-2024-01-05", kind="hour", group="quiet",
         start="2024-01-05T15", tag="s15h_20240105T15",
         note="Quiet hour: many MAJO alarms, one of them still confirmed by "
              "the network (a false event, or a small quake missing from the "
              "catalogue)."),
    dict(id="quiet-2021-11-05", kind="hour", group="quiet",
         start="2021-11-05T02", tag="s15h_20211105T02",
         note="Quiet hour: the network confirmed 2 events without any MAJO "
              "alarm (neighbors only)."),
]


# USGS catalog: event descriptions (cached so the build works without internet)
def usgs_meta(origins):
    cache = {}
    if os.path.exists(META_CACHE):
        with open(META_CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    missing = [o for o in origins if o not in cache]
    if missing:
        client = Client("USGS", timeout=120)
        for o in missing:
            t = UTCDateTime(o)
            cat = client.get_events(starttime=t - 30, endtime=t + 30,
                                    minmagnitude=4.0)
            ev = min(cat, key=lambda e: abs(
                (e.preferred_origin() or e.origins[0]).time - t))
            org = ev.preferred_origin() or ev.origins[0]
            mag = ev.preferred_magnitude() or ev.magnitudes[0]
            cache[o] = dict(
                time=str(org.time), mag=round(float(mag.mag), 1),
                mag_type=mag.magnitude_type, lat=org.latitude,
                lon=org.longitude,
                depth=None if org.depth is None else round(org.depth / 1000),
                place=ev.event_descriptions[0].text
                if ev.event_descriptions else "")
        with open(META_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=1)
    return cache


# station records of the scenario
def load_traces(sc):
    """{station: trace} and the list of stations without data"""
    traces = {}
    if sc["kind"] == "quake":
        start = UTCDateTime(sc["origin"]) - BEFORE
        end = UTCDateTime(sc["origin"]) + AFTER
    else:
        start = UTCDateTime(sc["start"] + ":00:00")
        end = start + 3600
    for code in [MAJO] + list(NEIGHBORS):
        if code == MAJO and sc.get("majo_file"):
            tr = majo_window(os.path.join(ROOT, sc["majo_file"])[:-6], start, end)
        else:
            path = os.path.join(ROOT, cache_path(code, sc["tag"]))
            if not os.path.exists(path):
                continue
            tr = read(path)[0]
            tr.trim(start, end)
        if tr.stats.npts:
            traces[code] = tr
    missing = [c for c in [MAJO] + list(NEIGHBORS) if c not in traces]
    return traces, missing, start, end


def envelope(tr):
    """signal after the 1-8 Hz filter (as the detector sees it) and STA/LTA per second"""
    tr_f, ratio, _ = analyze(tr)
    fs = tr.stats.sampling_rate
    n = max(1, int(round(ENV_STEP * fs)))
    data = tr_f.data
    bins = len(data) // n
    seg = data[:bins * n].reshape(bins, n)
    rseg = ratio[:bins * n].reshape(bins, n)
    lo, hi = seg.min(axis=1), seg.max(axis=1)
    # scale by the 99.9th percentile so that one spike does not flatten everything else
    scale = float(np.percentile(np.abs(data), 99.9)) or 1.0
    to_int = lambda a: np.clip(np.round(a / scale * 100), -100, 100).astype(int)
    return dict(
        t0=round(tr.stats.starttime.timestamp, 3), dt=ENV_STEP,
        lo=to_int(lo).tolist(), hi=to_int(hi).tolist(),
        ratio=np.round(np.minimum(rseg.max(axis=1), RATIO_CAP), 1).tolist(),
        rate=fs, channel=tr.id)


def run_through_server(traces):
    """run the records through the server and note WHEN the server learned about each alarm
    and when the event became confirmed (data time of the end of the chunk)"""
    alarms, first_conf = [], {}   # alarm id -> when it first ended up in a confirmed event
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        app = create_app(os.path.join(tmp, "s.db"), MIN_NET)
        client = TestClient(app)
        for chunk_end, code, payload in replay.make_chunks(traces, CHUNK_SEC):
            r = client.post(f"/api/ingest/{code}", json=payload)
            r.raise_for_status()
            new = r.json()["alarms"]
            if not new:
                continue
            for a in new:
                alarms.append(dict(id=a["id"], station=a["station"],
                                   ts=UTCDateTime(a["time"]).timestamp,
                                   peak=a["peak"],
                                   detected_at=chunk_end.timestamp))
            for ev in client.get("/api/events", params=dict(limit=100000)).json():
                if ev["status"] == "confirmed":
                    for a in ev["alarms"]:
                        first_conf.setdefault(a["id"], chunk_end.timestamp)
        final = client.get("/api/events", params=dict(limit=100000)).json()
        final_alarms = {a["id"]: a for a in client.get(
            "/api/alarms", params=dict(limit=100000)).json()}
        app.state.db.conn.close()
    for a in alarms:   # final event membership (after merges)
        a["event"] = final_alarms[a["id"]]["event_id"]
    events = []
    for ev in final:
        if ev["status"] != "confirmed":
            continue
        # if the event was formed by a merge, take the earliest confirmation
        # of any of its parts
        times = [first_conf[a["id"]] for a in ev["alarms"] if a["id"] in first_conf]
        events.append(dict(
            id=ev["id"], confirmed_at=min(times) if times else None,
            stations=sorted({a["station"] for a in ev["alarms"]}),
            first_ts=UTCDateTime(ev["t_first"]).timestamp))
    for e in events:
        if e["confirmed_at"] is None:
            e["confirmed_at"] = max(a["detected_at"] for a in alarms
                                    if a["event"] == e["id"])
    events.sort(key=lambda e: e["confirmed_at"])
    return alarms, events


def outcome(sc, alarms, events, origin_ts):
    """result of a scenario by the step 15 definitions (the origin time is exact, from the USGS catalog)"""
    if sc["kind"] == "quake":
        t = origin_ts
        majo = sorted((a for a in alarms if a["station"] == MAJO
                       and t <= a["ts"] <= t + EQ_WINDOW), key=lambda a: a["ts"])
        ok_ids = {e["id"] for e in events}
        seen = bool(majo)
        net = seen and majo[0]["event"] in ok_ids
        # for reference: did the network confirm any event with an alarm in the window
        # (by the step 15 definition this does not count if there is no MAJO alarm)
        in_win = {a["event"] for a in alarms if t <= a["ts"] <= t + EQ_WINDOW}
        any_ev = [e for e in events if e["id"] in in_win]
        conf_ev = next((e for e in events if seen and e["id"] == majo[0]["event"]), None)
        return dict(detected=seen, confirmed=bool(net),
                    delay=round(majo[0]["ts"] - t) if seen else None,
                    confirm_delay=round(conf_ev["confirmed_at"] - t) if conf_ev else None,
                    any_event_delay=round(min(e["confirmed_at"] for e in any_ev) - t)
                    if any_ev else None)
    return dict(majo_alarms=sum(a["station"] == MAJO for a in alarms),
                confirmed_events=len(events),
                events_without_majo=sum(MAJO not in e["stations"] for e in events))


# result numbers: taken from the step 14 and 15 outputs, nothing is typed in by hand
def must(pattern, text, what):
    m = re.search(pattern, text, re.M)
    if not m:
        raise SystemExit(f"Не нашёл в выводе: {what}")
    return m


def share(k, n):
    lo, hi = wilson(k, n)
    return dict(k=k, n=n, lo=round(lo, 3), hi=round(hi, 3))


def results_summary(meta_tuning):
    with open(os.path.join(ROOT, "results", "step14_output.txt"), encoding="utf-8") as f:
        s14 = f.read()
    with open(os.path.join(ROOT, "results", "step15_output.txt"), encoding="utf-8") as f:
        s15 = f.read()
    hours14 = int(must(r"^Тихих часов: (\d+)\.", s14, "число часов шага 14").group(1))
    m = must(r"^\s+сервер N=3 \(поток\)\s+(\d+) из (\d+)\s+(\d+)\s+(\d+)", s14,
             "строка 'сервер N=3' таблицы Б шага 14")
    majo14 = int(m.group(4))   # all MAJO alarms in the quiet hours of step 14
    m = must(r"^\s+3\s+(\d+) из (\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)\s+(\d+)\s*$", s14,
             "строка N=3 таблицы А шага 14")
    net_eq14, net_n14, _, ev14, _, _ = m.groups()
    tuning = dict(
        quakes=share(int(net_eq14), int(net_n14)), hours=hours14,
        majo_alarms=majo14, net_events=int(ev14),
        events=meta_tuning)

    m = must(r"замечено MAJO одной: (\d+)/(\d+)", s15, "доля MAJO шага 15")
    seen, n = int(m.group(1)), int(m.group(2))
    m = must(r"подтверждено сетью:\s+(\d+)/(\d+)", s15, "доля сети шага 15")
    net = int(m.group(1))
    bins = []
    for name in ("4.5-4.9", "5.0-5.3", "5.4-5.7", "<150 км", "150-300 км", ">300 км"):
        m = must(rf"^\s+{re.escape(name)}\s+n=(\d+)\s+MAJO: (\d+)/\d+.*сеть: (\d+)/\d+",
                 s15, f"строка {name}")
        nn, a, b = map(int, m.groups())
        bins.append(dict(name=name.replace("км", "km"),
                         kind="mag" if name[0].isdigit() and "-" in name and "км" not in name
                         else "dist",
                         majo=share(a, nn), net=share(b, nn)))
    m = must(r"одна станция \(MAJO\), тревог: (\d+) за (\d+) ч = ([\d.]+) в час; "
             r"пуассоновский интервал \[([\d.]+)-([\d.]+)\]; бутстрап по часам "
             r"\[([\d.]+)-([\d.]+)\]", s15, "ложные MAJO шага 15")
    majo_q = [float(x) for x in m.groups()]
    m = must(r"сеть N=3, подтверждённых событий: (\d+) за (\d+) ч = ([\d.]+) в час; "
             r"пуассоновский интервал \[([\d.]+)-([\d.]+)\]; бутстрап по часам "
             r"\[([\d.]+)-([\d.]+)\]", s15, "ложные события сети шага 15")
    net_q = [float(x) for x in m.groups()]
    no_majo = int(must(r"Подтверждённых событий без тревоги MAJO: (\d+)", s15,
                       "события без MAJO").group(1))
    events = []
    for m in re.finditer(
            r"^\s+(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\s+M([\d.]+)\s+глубина (\S+) км\s+"
            r"до MAJO (\d+) км\s+MAJO: (нет|[+-]\d+ с)\s+станций с тревогой в окне: "
            r"(\d+) \(данные есть у (\d+)\)\s+сеть: (ДА|нет)", s15, re.M):
        t, mag, depth, dist, delay, nal, ndat, netv = m.groups()
        events.append(dict(
            time=t, mag=float(mag), depth=None if depth == "?" else int(depth),
            dist=int(dist), delay=None if delay == "нет" else int(delay.split()[0]),
            stations_alarmed=int(nal), stations_with_data=int(ndat),
            confirmed=netv == "ДА"))
    if len(events) != n:
        raise SystemExit(f"В выводе шага 15 нашлось {len(events)} событий, а не {n}")
    m = must(r"M4\.5-5\.7 \(тип earthquake\): (\d+); рядом с событиями step13: (\d+); "
             r"другое M4\+ в \+-10 мин от окна: (\d+); в 30 мин после M5\.5\+: (\d+); "
             r"остаётся кандидатов: (\d+)", s15, "воронка отбора шага 15")
    funnel = dict(zip(("m45_57", "near_step13", "other_m4", "aftershock", "left"),
                      map(int, m.groups())))
    independent = dict(funnel=funnel,
        quakes=dict(majo=share(seen, n), net=share(net, n)), bins=bins,
        hours=int(majo_q[1]),
        majo_false=dict(count=int(majo_q[0]), rate=majo_q[2],
                        boot=[majo_q[5], majo_q[6]], poisson=[majo_q[3], majo_q[4]]),
        net_false=dict(count=int(net_q[0]), rate=net_q[2],
                       boot=[net_q[5], net_q[6]], poisson=[net_q[3], net_q[4]]),
        net_false_without_majo=no_majo, events=events)
    return dict(tuning=tuning, independent=independent)


def main():
    os.makedirs(DATA_OUT, exist_ok=True)
    with open(STATIONS_FILE, encoding="utf-8") as f:
        stations = json.load(f)
    majo_pos = next((s["lat"], s["lon"]) for s in stations if s["code"] == MAJO)

    origins = [sc["origin"] for sc in SCENARIOS if sc["kind"] == "quake"]
    from step12 import ORIGINS
    meta = usgs_meta(sorted(set(origins) | set(ORIGINS)))

    index = []
    for sc in SCENARIOS:
        traces, missing, start, end = load_traces(sc)
        print(f"{sc['id']}: станции {sorted(traces)}, нет данных: {missing}")
        alarms, events = run_through_server(traces)
        info, origin_ts = None, None
        if sc["kind"] == "quake":
            m = meta[sc["origin"]]
            dist = gps2dist_azimuth(*majo_pos, m["lat"], m["lon"])[0] / 1000
            info = dict(m, dist_km=round(dist))
            origin_ts = UTCDateTime(m["time"]).timestamp
        res = outcome(sc, alarms, events, origin_ts)
        title = (f"M{info['mag']} {info['place']}" if info
                 else f"Quiet hour {sc['start'].replace('T', ' ')}:00 UTC")
        end_ts = max([end.timestamp] + [a["detected_at"] for a in alarms]
                     + [e["confirmed_at"] for e in events])
        doc = dict(
            id=sc["id"], kind=sc["kind"], group=sc["group"], title=title,
            note=sc["note"], start=start.timestamp, end=end_ts,
            origin=origin_ts,
            usgs=info, missing=missing, outcome=res, alarms=alarms, events=events,
            traces={code: envelope(tr) for code, tr in traces.items()})
        with open(os.path.join(DATA_OUT, f"scn_{sc['id']}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(doc, f, separators=(",", ":"))
        index.append(dict(id=sc["id"], kind=sc["kind"], group=sc["group"],
                          title=title, note=sc["note"], outcome=res,
                          usgs=info, start=start.timestamp))
        print(f"   итог: {res}")

    tuning_meta = [dict(origin=o, **{k: meta[o][k] for k in
                                     ("mag", "mag_type", "depth", "place")})
                   for o in ORIGINS]
    summary = results_summary(tuning_meta)
    with open(os.path.join(DATA_OUT, "site.json"), "w", encoding="utf-8") as f:
        json.dump(dict(stations=stations, scenarios=index, results=summary,
                       rule=dict(min_stations=MIN_NET, window_s=60, threshold=10,
                                 sta_s=1, lta_s=30, band_hz=[1, 8]),
                       built=str(UTCDateTime())[:19] + "Z"),
                  f, ensure_ascii=False, indent=1)
    shutil.copy(os.path.join(ROOT, "results", "step15_detection.png"),
                os.path.join(DATA_OUT, "step15_detection.png"))
    open(os.path.join(SITE, ".nojekyll"), "w").close()   # serve the files as they are
    print(f"Готово: {SITE}")


if __name__ == "__main__":
    main()
