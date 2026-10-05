# quake-detector

An earthquake detector for Japan built on STA/LTA, plus a small backend that
combines several seismic stations into one network decision.

## How it works

**Single-station detector (IU.MAJO).** Band-pass filter 1-8 Hz, classic STA/LTA
with a 1 s short window and a 30 s long window, trigger threshold 10. Alarms
less than 60 s apart are merged into one. The same detector runs on every
station.

**Network rule.** The backend (`server/`) receives record streams from five
stations: IU.MAJO and its neighbors JP.JGF, JP.JSD, G.INU and PS.TSK. Alarms
from different stations within 60 s of each other form one event. An event is
**confirmed if alarms from at least 3 of the 5 stations** fall within 60 s.
Alarms and records are stored in SQLite. The number of stations is the
`QUAKE_MIN_STATIONS` environment variable (default 3). Chaining applies: an
alarm within 60 s of any alarm already in an event joins that event.

## Results

All numbers come from `results/step14_output.txt` (the recorded data replayed
through the server as if it were live) and `results/step13_output.txt`.

- **Earthquakes:** 10 of 10 confirmed (M5.8-6.0, Japan, 2011-2023). Each had a
  confirmed network event with an alarm in the 0..+120 s after origin time, and
  every one of those events contains a MAJO alarm. MAJO alone detected 10 of 10
  with a median delay of 48.5 s after origin time (the delay of the network
  confirmation was not measured separately).
- **Quiet periods:** 24 random hours (seed 42, 2016-2024; the USGS catalogue
  shows no M4+ event in the 30-45 N, 130-146 E box from 10 min before to 70 min
  after the hour, and no M5.5+ event anywhere in the 30 min before it).
  - one station (MAJO): 18 alarms, **0.75 per hour**;
  - network, 3 of 5 stations: 5 confirmed events, **0.21 per hour**, all of
    them containing a MAJO alarm;
  - for comparison, 2 of 5 stations also confirms 10 of 10 earthquakes, but
    gives 12 confirmed events (0.50 per hour), 5 of them without any MAJO
    alarm.

## Limitations

- The same data was used to choose the rule and to test it, so the numbers are
  not an independent validation.
- The sample is small: 10 earthquakes and 24 hours (18 single-station alarms,
  clustered in a few hours). A difference of one or two alarms is not a real
  difference.
- Weaker earthquakes (M5.0-5.6) were not tested, so we do not know the smallest
  event the system reliably catches. Only M5.8-6.0 events were checked.
- Tohoku 2011 is confirmed with no margin: exactly 3 stations alarmed, which is
  every station that had data (JP.JGF and JP.JSD have no records for that date,
  and the PS.TSK record ends shortly after the alarm).
- The network is five stations close to each other (the four neighbors are
  130-175 km from MAJO). If stations drop out, an event may become impossible
  to confirm: with three of the five down, the 3-station rule can never be met.
- Real-time behavior was not tested: the server was fed recorded files without
  delays, network gaps or dropped connections. The streaming detector ignores
  the first 60 s of a stream (warm-up), and its state is kept in memory, so a
  server restart starts with an empty buffer.
- The 5 remaining "false" events were only filtered through the USGS catalogue
  and may be small real events missing from it.

## Repository layout

- `step1.py` ... `step13.py` - the research steps (detector, tests on
  earthquakes and quiet hours, neighbor stations, rule comparison). `step11.py`
  holds the detector code reused by everything else.
- `server/` - backend: streaming detector, network rule, SQLite storage, HTTP
  API, replay of recorded files, and the step 14 evaluation (`server/README.md`).
- `web/index.html` - single-page UI served by the backend at `/`: map, confirmed
  events, per-station signal plots, auto-refresh every 2 s.
- `results/` - saved outputs of the steps, including `step14_output.txt`.
- `requirements.txt` for the research scripts, `requirements-server.txt` for
  the backend.

## Quick demo (Windows)

Double-click `run_demo.bat`. On the first run it creates a virtual environment,
installs the libraries and downloads the neighbor-station records (internet and
Python 3.10+ are needed once). Then it starts the server in a separate window,
opens http://127.0.0.1:8000 in your browser and replays the 2021-10-07
earthquake at 60x speed, so you can watch the events appear on the map. When you
are done, close the server window ("Quake Detector SERVER").

Quick start (Linux/macOS; on Windows use `.venv\Scripts\python`):

    python -m venv .venv
    .venv/bin/pip install -r requirements.txt -r requirements-server.txt
    .venv/bin/uvicorn server.app:create_app --factory --port 8000
    .venv/bin/python -m server.replay quake:2021-10-07   # needs data/ from step13.py
