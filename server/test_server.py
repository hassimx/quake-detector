"""Проверки бэкенда. Запуск из корня: .venv/bin/python -m pytest server -q

Тесты на реальных записях пропускаются, если нет файлов data/ (их создаёт
step13.py).
"""

import os

import pytest
from fastapi.testclient import TestClient
from obspy import UTCDateTime, read

from server import replay
from server.app import create_app
from server.detector import StreamDetector
from step11 import analyze
from step13 import cache_path

HOUR_TAG = "hour_20160611T17"
needs_data = pytest.mark.skipif(
    not os.path.exists(cache_path("IU.MAJO", HOUR_TAG)),
    reason="нет data/: сначала запустите step13.py")


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "t.db"), min_stations=2))


def chunk(start, samples, fs=20.0):
    return dict(starttime=str(UTCDateTime(start)), sampling_rate=fs,
                samples=samples)


def test_unknown_station(client):
    assert client.post("/api/ingest/XX.YYY", json=chunk(0, [0.0])).status_code == 404


def test_stations_and_config(client):
    assert len(client.get("/api/stations").json()) == 5
    assert client.get("/api/config").json() == dict(min_stations=2)


@needs_data
def test_stream_matches_batch():
    """Потоковый детектор находит те же тревоги, что пакетный (step11)."""
    tr = read(cache_path("IU.MAJO", HOUR_TAG))[0]
    _, _, batch = analyze(tr)
    det, stream = StreamDetector(), []
    size = int(10 * tr.stats.sampling_rate)
    for i in range(0, tr.stats.npts, size):
        stream += det.push(tr.stats.starttime + i / tr.stats.sampling_rate,
                           tr.stats.sampling_rate, tr.data[i:i + size])
    assert len(stream) == len(batch)
    for s, b in zip(stream, batch):
        assert abs(s["time"] - b) < 3  # секунд


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
