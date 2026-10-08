"""HTTP server: takes chunks of station records and returns alarms and events.

Run (from the project root):
    .venv/bin/uvicorn server.app:create_app --factory --port 8000
Settings through environment variables:
    QUAKE_DB            path to the database file (default server/quake.db)
    QUAKE_MIN_STATIONS  how many stations are needed to confirm an event
                        (default 3; in step 14, 3 gave 0.21 false events per
                        hour against 0.50 with 2)
"""

import json
import os
import threading

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from obspy import UTCDateTime
from pydantic import BaseModel

from server.db import Database
from server.detector import StreamDetector
from server.network import link_alarm

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "quake.db")
STATIONS_FILE = os.path.join(HERE, "stations.json")
INDEX_FILE = os.path.join(HERE, "..", "web", "index.html")  # page for the browser
DEFAULT_MIN_STATIONS = 3  # chosen from the step 14 results (results/step14_output.txt)


class Chunk(BaseModel):
    """a chunk of a station record: time of the first sample, rate and the samples themselves"""
    starttime: str
    sampling_rate: float
    samples: list[float]


def iso(ts):
    return UTCDateTime(ts).isoformat() + "Z" if ts is not None else None


def create_app(db_path=None, min_stations=None):
    db_path = db_path or os.environ.get("QUAKE_DB", DEFAULT_DB)
    min_stations = min_stations or int(
        os.environ.get("QUAKE_MIN_STATIONS", DEFAULT_MIN_STATIONS))
    db = Database(db_path)
    with open(STATIONS_FILE, encoding="utf-8") as f:
        for s in json.load(f):
            db.add_station(s["code"], s["name"], s["lat"], s["lon"])
    db.commit()
    known = {s["code"] for s in db.stations()}
    detectors = {code: StreamDetector() for code in known}  # state in memory
    lock = threading.Lock()

    app = FastAPI(title="quake-detector")

    @app.get("/", include_in_schema=False)
    def index():
        """web page with the map and the event list (web/index.html)"""
        return FileResponse(INDEX_FILE, media_type="text/html")

    @app.get("/api/config")
    def config():
        return dict(min_stations=min_stations)

    @app.get("/api/stations")
    def stations():
        return db.stations()

    @app.post("/api/ingest/{station}")
    def ingest(station: str, chunk: Chunk):
        if station not in known:
            raise HTTPException(404, f"неизвестная станция {station}")
        start = UTCDateTime(chunk.starttime)
        with lock:
            db.add_chunk(station, start.timestamp, chunk.sampling_rate,
                         chunk.samples)
            found = []
            for a in detectors[station].push(
                    start, chunk.sampling_rate, chunk.samples):
                alarm_id = db.add_alarm(station, a["time"].timestamp, a["peak"])
                event_id = link_alarm(db, alarm_id, station,
                                      a["time"].timestamp, min_stations)
                found.append(dict(id=alarm_id, station=station,
                                  time=iso(a["time"].timestamp),
                                  peak=round(a["peak"], 1), event_id=event_id))
            db.commit()
        return dict(alarms=found)

    @app.get("/api/alarms")
    def alarms(limit: int = 200, station: str | None = None):
        rows = db.alarms(limit, station)
        return [dict(r, time=iso(r.pop("time_ts"))) for r in rows]

    @app.get("/api/events")
    def events(limit: int = 100, status: str | None = None):
        out = []
        for e in db.events(limit):
            if status and e["status"] != status:
                continue
            e["t_first"], e["t_last"] = iso(e["t_first"]), iso(e["t_last"])
            e["alarms"] = [dict(a, time=iso(a.pop("time_ts")))
                           for a in e["alarms"]]
            out.append(e)
        return out

    @app.get("/api/waveform")
    def waveform(station: str, start: str, end: str, max_points: int = 2000):
        t, v = db.waveform(station, UTCDateTime(start).timestamp,
                           UTCDateTime(end).timestamp, max_points)
        return dict(station=station, times=[iso(x) for x in t], values=v)

    app.state.db = db  # so scripts (evaluate.py) can close the database
    return app
