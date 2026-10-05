"""Шаг 16 (разведка): можно ли получать данные станций в реальном времени (SeedLink)?

Только разведка, постоянного запуска нет. Скрипт:
  1) подключается к публичному SeedLink-серверу EarthScope и берёт список потоков;
  2) проверяет, есть ли там наши IU.MAJO, JP.JGF, JP.JSD, G.INU, PS.TSK и какие
     ещё станции в рамке Японии (30-45 с.ш., 130-146 в.д.) отдают данные вживую;
  3) 60 секунд принимает живые данные и считает отсчёты и задержку пакетов
     относительно текущего времени компьютера (часы должны быть точными);
  4) пишет отчёт в results/step16_livecheck.txt.

Запуск (Windows):  .venv\\Scripts\\python step16_livecheck.py
Нужен выход в интернет на порт 18000 (SeedLink); корпоративные сети его часто
закрывают, тогда скрипт честно напишет об этом в отчёте.

Откуда адрес сервера (в коде он не угадан): объявление EarthScope
"SeedLink service is moving as part of our cloud transition"
(https://ngf.earthscope.org/news/seedlink-service-moving-part-our-cloud-transition)
и документация https://docs.earthscope.org/service/seedlink : сервис
rtserve.earthscope.org, порт 18000 (обычный SeedLink), 18500 (SeedLink по TLS),
443 (WebSocket); не более 5 одновременных соединений, keepalive не чаще раза в
4 минуты; старый адрес rtserve.iris.washington.edu переведён на новый сервис.
ObsPy умеет только обычный SeedLink (порт 18000): TLS и WebSocket мы не проверяли.
"""

import os
import statistics
import threading
import time

from obspy import UTCDateTime
from obspy.clients.fdsn import Client as FDSNClient
from obspy.clients.fdsn.header import FDSNNoDataException
from obspy.clients.seedlink.basic_client import Client as InfoClient
from obspy.clients.seedlink.easyseedlink import EasySeedLinkClient
from obspy.geodetics import gps2dist_azimuth

HOST, PORT = "rtserve.earthscope.org", 18000
OUTPUT_FILE = os.path.join("results", "step16_livecheck.txt")
OURS = ["IU.MAJO", "JP.JGF", "JP.JSD", "G.INU", "PS.TSK"]
BOX = dict(minlatitude=30, maxlatitude=45, minlongitude=130, maxlongitude=146)
MAJO_POS = (36.54567, 138.20406)
LISTEN_SEC = 60
MAX_OTHER = 40        # сколько других японских станций слушаем одновременно
TIMEOUT = 30          # таймаут сети для запросов списка, сек

lines = []


def say(text=""):
    """Печатает строку и запоминает её для отчёта."""
    print(text, flush=True)
    lines.append(text)


def save():
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nОтчёт сохранён в {OUTPUT_FILE}")


def pick_z_channel(streams):
    """Из потоков станции [(loc, cha)] берём вертикальный канал: BHZ, потом HHZ."""
    for wanted in ("BHZ", "HHZ"):
        found = sorted(s for s in streams if s[1] == wanted)
        if found:
            return "00" if ("00", wanted) in found else found[0][0], wanted
    return None


def japanese_stations_from_fdsn():
    """{'сеть.станция': (широта, долгота)} в рамке Японии, работающие сейчас."""
    now = UTCDateTime()
    client = FDSNClient("EARTHSCOPE", timeout=TIMEOUT)
    try:
        inv = client.get_stations(
            network="*", station="*", channel="BHZ,HHZ", level="station",
            starttime=now - 86400, endtime=now, **BOX)
    except FDSNNoDataException:
        return {}
    return {f"{n.code}.{s.code}": (s.latitude, s.longitude)
            for n in inv for s in n}


class Stop(Exception):
    """Выход из бесконечного цикла приёма по истечении времени."""


class Listener(EasySeedLinkClient):
    """Принимает пакеты, пока не пройдёт LISTEN_SEC секунд."""

    def __init__(self, url):
        super().__init__(url)
        self.deadline = None
        self.stats = {}   # (сеть, станция, loc, канал) -> {samples, rate, delays, lengths}

    def on_data(self, trace):
        now = UTCDateTime()
        if self.deadline is None:
            self.deadline = time.time() + LISTEN_SEC
        s = trace.stats
        key = (s.network, s.station, s.location, s.channel)
        rec = self.stats.setdefault(key, dict(samples=0, rate=s.sampling_rate,
                                              delays=[], lengths=[], packets=0))
        rec["samples"] += s.npts
        rec["packets"] += 1
        rec["delays"].append(now - s.endtime)   # от последнего отсчёта пакета до приёма
        rec["lengths"].append(s.npts / s.sampling_rate)
        if time.time() >= self.deadline:
            raise Stop()

    def on_terminate(self):
        say("  Сервер закрыл соединение.")


def listen(selectors):
    """Слушает выбранные потоки LISTEN_SEC секунд. Возвращает (stats, ошибка)."""
    holder = {}

    def work():
        try:
            client = Listener(f"{HOST}:{PORT}")
            holder["client"] = client
            for net, sta, selector in selectors:
                client.select_stream(net, sta, selector)
            client.run()
        except Stop:
            pass
        except Exception as err:
            holder["error"] = err

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(timeout=LISTEN_SEC + 40)   # запас на подключение
    client = holder.get("client")
    stats = client.stats if client else {}
    if thread.is_alive():
        holder.setdefault("error", RuntimeError("приём завис, данные не пришли вовремя"))
    return stats, holder.get("error")


def main():
    say(f"Разведка SeedLink, {UTCDateTime()} UTC")
    say(f"Сервер: {HOST}:{PORT}")
    say("Откуда адрес: объявление EarthScope 'SeedLink service is moving as part of "
        "our cloud transition' (https://ngf.earthscope.org/news/seedlink-service-"
        "moving-part-our-cloud-transition) и документация "
        "https://docs.earthscope.org/service/seedlink. Там сказано: порт 18000 "
        "(SeedLink), 18500 (TLS), 443 (WebSocket); не более 5 одновременных "
        "соединений; keepalive не чаще раза в 4 минуты; старый адрес "
        "rtserve.iris.washington.edu теперь указывает на новый сервис.")
    say("Оговорка: страницы документации не удалось открыть напрямую "
        "(доступ закрыт), цитаты взяты из результатов веб-поиска; проверьте их "
        "по ссылкам выше.")
    say()

    # --- 1. Список потоков ---
    say("=== 1. Список потоков ===")
    try:
        channels = InfoClient(HOST, PORT, timeout=TIMEOUT).get_info(
            level="channel", cache=False)
    except Exception as err:
        say(f"НЕ УДАЛОСЬ подключиться к {HOST}:{PORT} и получить список потоков.")
        say(f"Причина: {type(err).__name__}: {err}")
        say("Это значит только то, что из ЭТОЙ сети сервер недоступен (часто закрыт "
            "порт 18000 или нет интернета). Запустите скрипт на своём компьютере: "
            r".venv\Scripts\python step16_livecheck.py")
        say("Дальше (наличие станций, приём 60 с, задержки) без соединения проверить "
            "нельзя, ничего не придумываю.")
        save()
        return
    stations = {}
    for net, sta, loc, cha in channels:
        stations.setdefault(f"{net}.{sta}", set()).add((loc, cha))
    nets = sorted({k.split(".")[0] for k in stations})
    say(f"Потоков с данными в буфере сервера: {len(channels)}, станций: "
        f"{len(stations)}, сетей: {len(nets)}")

    # --- 2. Наши станции ---
    say()
    say("=== 2. Наши станции ===")
    selected = []   # (net, sta, selector)
    available = []
    for code in OURS:
        streams = stations.get(code)
        pick = pick_z_channel(streams) if streams else None
        if pick is None:
            say(f"  {code}: НЕТ вживую" + (" (станция есть, но без BHZ/HHZ)" if streams else ""))
            continue
        loc, cha = pick
        available.append(code)
        net, sta = code.split(".")
        selected.append((net, sta, f"{loc}{cha}"))
        say(f"  {code}: есть, берём {loc or '--'}.{cha} "
            f"(всего потоков станции: {len(streams)})")
    say(f"Доступно вживую наших станций: {len(available)} из {len(OURS)}")

    # --- 3. Другие японские станции ---
    say()
    say("=== 3. Другие станции в рамке Японии (30-45 с.ш., 130-146 в.д.) ===")
    try:
        japan = japanese_stations_from_fdsn()
    except Exception as err:
        japan = {}
        say(f"Каталог станций FDSN недоступен ({type(err).__name__}: {err}); "
            "список японских станций построить не из чего.")
    others = []
    for code, (lat, lon) in japan.items():
        if code in OURS or code not in stations:
            continue
        pick = pick_z_channel(stations[code])
        if pick:
            dist = gps2dist_azimuth(*MAJO_POS, lat, lon)[0] / 1000
            others.append((dist, code, pick))
    others.sort()
    say(f"Станций рамки в каталоге FDSN: {len(japan)}; из них отдают данные вживую "
        f"(BHZ/HHZ), кроме наших: {len(others)}")
    for dist, code, (loc, cha) in others:
        say(f"  {code:10} {loc or '--'}.{cha}   {dist:6.0f} км от MAJO")
    for dist, code, (loc, cha) in others[:MAX_OTHER]:
        net, sta = code.split(".")
        selected.append((net, sta, f"{loc}{cha}"))

    # --- 4. Приём живых данных ---
    say()
    say(f"=== 4. Приём {LISTEN_SEC} с живых данных ({len(selected)} станций, одно соединение) ===")
    if not selected:
        say("Нечего слушать: ни одной подходящей станции.")
        save()
        return
    t_start = UTCDateTime()
    stats, error = listen(selected)
    if error:
        say(f"Приём закончился с ошибкой: {type(error).__name__}: {error}")
    say(f"Приём начат {t_start} UTC (по часам этого компьютера)")
    say("  станция      канал    Гц  пакетов  отсчётов  длина пакета, с  задержка до приёма, с "
        "(мин / медиана / макс)")
    for (net, sta, loc, cha), r in sorted(stats.items()):
        d = r["delays"]
        mark = "  <- наша" if f"{net}.{sta}" in OURS else ""
        say(f"  {net + '.' + sta:12} {loc or '--'}.{cha}  {r['rate']:4g}  {r['packets']:7}  "
            f"{r['samples']:8}  {statistics.median(r['lengths']):13.1f}   "
            f"{min(d):6.1f} / {statistics.median(d):6.1f} / {max(d):6.1f}{mark}")
    silent = [f"{n}.{s}" for n, s, _ in selected if not any(
        k[0] == n and k[1] == s for k in stats)]
    if silent:
        say(f"Выбраны, но данных за {LISTEN_SEC} с не пришло: {', '.join(silent)}")
    got_ours = [c for c in available if any(f"{k[0]}.{k[1]}" == c for k in stats)]
    say()
    say(f"Наши станции, от которых пришли данные: {len(got_ours)} из {len(OURS)} "
        f"({', '.join(got_ours) or '-'})")
    say("Задержка = время приёма минус время последнего отсчёта в пакете; она "
        "включает длину пакета (данные приходят кусками) и точность часов компьютера.")
    save()


if __name__ == "__main__":
    main()
