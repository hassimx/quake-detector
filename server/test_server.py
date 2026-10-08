"""backend checks. Run from the root: .venv/bin/python -m pytest server -q

Tests on real records are skipped if there are no data/ files (step13.py
creates them).
"""

import os

import pytest
from fastapi.testclient import TestClient
from obspy import UTCDateTime, read

from server import replay
from server.app import create_app
from server.db import Database
from server.detector import StreamDetector
from server.network import link_alarm
from step11 import analyze
from step13 import cache_path

HOUR_TAG = "hour_20160611T17"
needs_data = pytest.mark.skipif(
    not os.path.exists(cache_path("IU.MAJO", HOUR_TAG)),
    reason="нет data/: сначала запустите step13.py")


@pytest.fixture(params=[2, 3])
def client(request, tmp_path):
    """server with a confirmation threshold of 2 and 3 stations (tests run for both)"""
    app = create_app(str(tmp_path / "t.db"), min_stations=request.param)
    c = TestClient(app)
    c.min_stations = request.param
    return c


def chunk(start, samples, fs=20.0):
    return dict(starttime=str(UTCDateTime(start)), sampling_rate=fs,
                samples=samples)


def test_unknown_station(client):
    assert client.post("/api/ingest/XX.YYY", json=chunk(0, [0.0])).status_code == 404


def test_stations_and_config(client):
    assert len(client.get("/api/stations").json()) == 5
    assert client.get("/api/config").json() == dict(
        min_stations=client.min_stations)


def test_default_min_stations_is_3(tmp_path, monkeypatch):
    monkeypatch.delenv("QUAKE_MIN_STATIONS", raising=False)
    c = TestClient(create_app(str(tmp_path / "a.db")))
    assert c.get("/api/config").json() == dict(min_stations=3)


def test_min_stations_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("QUAKE_MIN_STATIONS", "2")
    c = TestClient(create_app(str(tmp_path / "b.db")))
    assert c.get("/api/config").json() == dict(min_stations=2)


@pytest.mark.parametrize("need", [2, 3])
def test_rule_needs_enough_stations(tmp_path, need):
    """two stations confirm an event only with threshold 2, three with threshold 3"""
    db = Database(str(tmp_path / "r.db"))
    t = 1_000_000.0
    status = []
    for i, station in enumerate(["IU.MAJO", "JP.JGF", "G.INU"]):
        alarm = db.add_alarm(station, t + 20 * i, 10.0)
        event = link_alarm(db, alarm, station, t + 20 * i, need)
        status.append(db.events()[0]["status"] if event else None)
    # after the 1st alarm there is no event, after the 2nd and 3rd we check the status
    assert status[0] is None
    assert status[1] == ("confirmed" if need == 2 else "candidate")
    assert status[2] == "confirmed"


@needs_data
def test_stream_matches_batch():
    """the streaming detector finds the same alarms as the batch one (step11)"""
    tr = read(cache_path("IU.MAJO", HOUR_TAG))[0]
    _, _, batch = analyze(tr)
    det, stream = StreamDetector(), []
    size = int(10 * tr.stats.sampling_rate)
    for i in range(0, tr.stats.npts, size):
        stream += det.push(tr.stats.starttime + i / tr.stats.sampling_rate,
                           tr.stats.sampling_rate, tr.data[i:i + size])
    assert len(stream) == len(batch)
    for s, b in zip(stream, batch):
        assert abs(s["time"] - b) < 3  # seconds


@needs_data
def test_quake_is_confirmed_and_db_filled(client):
    alarms = replay.run("quake:2021-10-07", client, verbose=False)
    events = client.get("/api/events", params=dict(status="confirmed")).json()
    assert events and events[0]["n_stations"] >= 2
    stations = {a["station"] for a in events[0]["alarms"]}
    assert "IU.MAJO" in stations
    assert len(client.get("/api/alarms").json()) == len(alarms)
    w = client.get("/api/waveform", params=dict(
        station="IU.MAJO", start="2021-10-07T13:41:00",
        end="2021-10-07T13:43:00")).json()
    assert w["values"]


def test_index_page(client):
    """the server root returns the web page, which calls the right APIs"""
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    for text in ("leaflet", "/api/stations", "/api/events?status=confirmed",
                 "/api/waveform", "How it works"):
        assert text in r.text


@needs_data
def test_page_data_after_replay(client):
    """after replay everything the page draws is there: stations, events, signal"""
    replay.run("quake:2021-10-07", client, verbose=False)
    assert client.get("/").status_code == 200
    stations = client.get("/api/stations").json()
    assert all({"code", "name", "lat", "lon"} <= set(s) for s in stations)
    events = client.get("/api/events", params=dict(status="confirmed")).json()
    assert events
    ev = events[0]
    assert {"id", "t_first", "t_last", "n_stations", "alarms"} <= set(ev)
    assert all({"station", "time"} <= set(a) for a in ev["alarms"])
    names = {s["code"] for s in stations}
    assert {a["station"] for a in ev["alarms"]} <= names
    w = client.get("/api/waveform", params=dict(
        station=ev["alarms"][0]["station"], start=ev["t_first"],
        end=ev["t_last"], max_points=1500)).json()
    assert len(w["times"]) == len(w["values"]) > 0
