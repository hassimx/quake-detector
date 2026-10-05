"""База данных (SQLite): станции, куски записей, тревоги и события сети."""

import sqlite3
import threading
import zlib

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS stations (
    code TEXT PRIMARY KEY, name TEXT, lat REAL, lon REAL);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY, station TEXT NOT NULL, start_ts REAL NOT NULL,
    sampling_rate REAL NOT NULL, npts INTEGER NOT NULL, data BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS chunks_station_time ON chunks (station, start_ts);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, t_first REAL, t_last REAL,
    n_stations INTEGER, status TEXT);
CREATE TABLE IF NOT EXISTS alarms (
    id INTEGER PRIMARY KEY, station TEXT NOT NULL, time_ts REAL NOT NULL,
    peak REAL, event_id INTEGER REFERENCES events (id));
CREATE INDEX IF NOT EXISTS alarms_time ON alarms (time_ts);
"""


class Database:
    """Тонкая обёртка над sqlite3. Все времена хранятся как UNIX-секунды (UTC)."""

    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.lock = threading.RLock()  # один писатель за раз

    # --- станции ---
    def add_station(self, code, name, lat, lon):
        self.conn.execute(
            "INSERT OR REPLACE INTO stations VALUES (?, ?, ?, ?)",
            (code, name, lat, lon))

    def stations(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM stations ORDER BY code")]

    # --- записи ---
    def add_chunk(self, station, start_ts, fs, samples):
        # float32 + zlib: в несколько раз компактнее, чем текст
        blob = zlib.compress(np.asarray(samples, dtype=np.float32).tobytes())
        self.conn.execute(
            "INSERT INTO chunks (station, start_ts, sampling_rate, npts, data)"
            " VALUES (?, ?, ?, ?, ?)", (station, start_ts, fs, len(samples), blob))

    def waveform(self, station, t0, t1, max_points=2000):
        """Отсчёты станции между t0 и t1: (времена, значения), не более max_points."""
        rows = self.conn.execute(
            "SELECT * FROM chunks WHERE station = ? AND start_ts <= ?"
            " AND start_ts + npts / sampling_rate >= ? ORDER BY start_ts",
            (station, t1, t0)).fetchall()
        times, values = [], []
        for r in rows:
            data = np.frombuffer(zlib.decompress(r["data"]), dtype=np.float32)
            t = r["start_ts"] + np.arange(len(data)) / r["sampling_rate"]
            keep = (t >= t0) & (t <= t1)
            times.append(t[keep])
            values.append(data[keep])
        if not times:
            return [], []
        times, values = np.concatenate(times), np.concatenate(values)
        step = max(1, len(times) // max_points)
        return times[::step].tolist(), values[::step].tolist()

    # --- тревоги и события ---
    def add_alarm(self, station, time_ts, peak):
        cur = self.conn.execute(
            "INSERT INTO alarms (station, time_ts, peak) VALUES (?, ?, ?)",
            (station, time_ts, peak))
        return cur.lastrowid

    def alarms_near(self, t0, t1, exclude_station):
        """Тревоги ДРУГИХ станций в интервале [t0, t1]."""
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM alarms WHERE time_ts BETWEEN ? AND ?"
            " AND station != ?", (t0, t1, exclude_station))]

    def alarms(self, limit=200, station=None):
        sql = "SELECT * FROM alarms"
        args = []
        if station:
            sql += " WHERE station = ?"
            args.append(station)
        sql += " ORDER BY time_ts DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args)]

    def create_event(self):
        cur = self.conn.execute(
            "INSERT INTO events (n_stations, status) VALUES (0, 'candidate')")
        return cur.lastrowid

    def assign(self, alarm_ids, event_id):
        marks = ",".join("?" * len(alarm_ids))
        self.conn.execute(
            f"UPDATE alarms SET event_id = ? WHERE id IN ({marks})",
            [event_id, *alarm_ids])

    def merge_events(self, old_ids, target_id):
        """Переносит тревоги старых событий в target и удаляет старые события."""
        for old in old_ids:
            self.conn.execute("UPDATE alarms SET event_id = ? WHERE event_id = ?",
                              (target_id, old))
            self.conn.execute("DELETE FROM events WHERE id = ?", (old,))

    def refresh_event(self, event_id, min_stations):
        """Пересчитывает событие по его тревогам."""
        row = self.conn.execute(
            "SELECT MIN(time_ts), MAX(time_ts), COUNT(DISTINCT station)"
            " FROM alarms WHERE event_id = ?", (event_id,)).fetchone()
        status = "confirmed" if row[2] >= min_stations else "candidate"
        self.conn.execute(
            "UPDATE events SET t_first = ?, t_last = ?, n_stations = ?,"
            " status = ? WHERE id = ?", (row[0], row[1], row[2], status, event_id))

    def events(self, limit=100):
        out = []
        for e in self.conn.execute(
                "SELECT * FROM events ORDER BY t_first DESC LIMIT ?", (limit,)):
            ev = dict(e)
            ev["alarms"] = [dict(a) for a in self.conn.execute(
                "SELECT id, station, time_ts, peak FROM alarms"
                " WHERE event_id = ? ORDER BY time_ts", (e["id"],))]
            out.append(ev)
        return out

    def commit(self):
        self.conn.commit()
