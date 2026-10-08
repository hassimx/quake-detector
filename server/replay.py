"""replay of recorded files as if the stations were sending data online.

Examples (from the project root, the server is already running):
    .venv/bin/python -m server.replay quake:2021-10-07 --speed 60
    .venv/bin/python -m server.replay hour:2016-06-11T17 --speed 0
Scenarios:
    quake:YYYY-MM-DD      window -15..+10 minutes around the origin from step12/13
    hour:YYYY-MM-DDTHH    quiet hour from step13
The records come from data/ (downloaded by step13.py) and ev_*.mseed (MAJO, as
in step12). --speed N: how many times faster than real time (0 = no pauses).
"""

import argparse
import os
import time

import httpx
from obspy import UTCDateTime, read

from step12 import AFTER, BEFORE, ORIGINS, NEIGHBORS, majo_window
from step13 import HOUR_SEC, cache_path

MAJO = "IU.MAJO"


def load_scenario(name):
    """returns {station code: trace} for the scenario"""
    kind, _, arg = name.partition(":")
    traces = {}
    if kind == "quake":
        text = next((o for o in ORIGINS if o.startswith(arg)), None)
        if text is None:
            raise SystemExit(f"нет землетрясения {arg} в списке step12")
        origin = UTCDateTime(text)
        start, end = origin - BEFORE, origin + AFTER
        traces[MAJO] = majo_window("ev_" + arg, start, end)
        tag = "quake_" + arg
    elif kind == "hour":
        t0 = UTCDateTime(arg + ":00:00")
        tag = t0.strftime("hour_%Y%m%dT%H")
        path = cache_path(MAJO, tag)
        if not os.path.exists(path):
            raise SystemExit(f"нет файла {path}: сначала запустите step13.py")
        traces[MAJO] = read(path)[0]
    else:
        raise SystemExit("сценарий: quake:ГГГГ-ММ-ДД или hour:ГГГГ-ММ-ДДTЧЧ")
    for key in NEIGHBORS:
        path = cache_path(key, tag)
        if os.path.exists(path):
            traces[key] = read(path)[0]
        else:
            print(f"  {key}: нет файла {path}, станция в сценарии не участвует")
    return traces


def make_chunks(traces, chunk_sec):
    """cuts the records into chunks and sorts them by the time of the end of the chunk"""
    chunks = []
    for code, tr in traces.items():
        fs = tr.stats.sampling_rate
        size = int(chunk_sec * fs)
        for i in range(0, tr.stats.npts, size):
            data = tr.data[i:i + size]
            start = tr.stats.starttime + i / fs
            chunks.append((start + len(data) / fs, code, dict(
                starttime=str(start), sampling_rate=fs,
                samples=data.astype(float).tolist())))
    chunks.sort(key=lambda c: c[0])
    return chunks


def run(scenario, client, chunk_sec=10, speed=0, verbose=True):
    """sends the chunks to the server through client (httpx.Client or TestClient).

    Returns the list of alarms that the server reported.
    """
    chunks = make_chunks(load_scenario(scenario), chunk_sec)
    alarms, prev = [], None
    for end, code, payload in chunks:
        if speed and prev is not None:
            time.sleep(max(0.0, (end - prev) / speed))
        prev = end
        resp = client.post(f"/api/ingest/{code}", json=payload)
        resp.raise_for_status()
        for a in resp.json()["alarms"]:
            alarms.append(a)
            if verbose:
                mark = f"событие {a['event_id']}" if a["event_id"] else "одиночная"
                print(f"  тревога {a['station']:8} {a['time']}  "
                      f"пик {a['peak']:5}  {mark}")
    return alarms


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("scenario")
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--speed", type=float, default=0)
    p.add_argument("--chunk", type=float, default=10, help="длина куска, сек")
    args = p.parse_args()
    with httpx.Client(base_url=args.url, timeout=60) as client:
        alarms = run(args.scenario, client, args.chunk, args.speed)
    print(f"Всего тревог: {len(alarms)}")


if __name__ == "__main__":
    main()
