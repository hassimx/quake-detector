"""demo preparation: downloads the records of the 4 neighbor stations for quake:2021-10-07.

Run (from the project root; run_demo.bat calls it too):
    .venv\\Scripts\\python -m server.prepare_demo

What it does:
  * takes the window from -15 to +10 minutes around the origin
    2021-10-07T13:41:24 (as in step12/13);
  * puts the records of JP.JGF, JP.JSD, G.INU, PS.TSK into data/ under the names
    that server/replay.py expects (step13 uses the same ones);
  * the MAJO record is already in the repository (ev_2021-10-07.mseed), so it
    is not downloaded;
  * if all files are already there, it downloads nothing;
  * on an error (for example, no internet) it prints a clear message, without
    a traceback.
Exit code: 0 - everything is ready, 1 - the demo cannot be started.
"""

import os
import sys

from obspy import UTCDateTime
from obspy.clients.fdsn import Client

from step12 import AFTER, BEFORE, NEIGHBORS, ORIGINS
from step13 import cache_path, load_neighbor

DATE = "2021-10-07"
TAG = "quake_" + DATE
MAJO_FILE = f"ev_{DATE}.mseed"
MIN_NEIGHBORS = 2  # MAJO + 2 neighbors = 3 stations, the rule cannot be met with fewer


def main():
    if not os.path.exists(MAJO_FILE):
        print(f"Не найден файл {MAJO_FILE} (запись MAJO должна лежать в корне "
              "проекта). Запускайте демо из папки проекта.")
        return 1
    os.makedirs("data", exist_ok=True)

    missing = [k for k in NEIGHBORS if not os.path.exists(cache_path(k, TAG))]
    if not missing:
        print("Все записи станций уже лежат в data/, скачивать ничего не нужно.")
        return 0

    origin = UTCDateTime(next(o for o in ORIGINS if o.startswith(DATE)))
    start, end = origin - BEFORE, origin + AFTER
    print(f"Скачиваю записи станций: {', '.join(missing)} "
          f"(окно {start} - {end}). Это может занять минуту-две...")
    try:
        client = Client("EARTHSCOPE", timeout=120)
    except Exception as err:
        return no_internet(err)

    no_data, errors = [], []
    for key in missing:
        tr, note = load_neighbor(client, key, start, end, TAG)
        if tr is not None:
            print(f"  {key}: готово ({tr.id}, {tr.stats.sampling_rate:g} Гц)")
        elif note.startswith("ОШИБКА"):
            errors.append(key)
            print(f"  {key}: не удалось скачать")
        else:
            no_data.append(key)
            print(f"  {key}: у сервера нет записи за эту дату")
    if errors:
        return no_internet(f"не скачались: {', '.join(errors)}")

    have = [k for k in NEIGHBORS if os.path.exists(cache_path(k, TAG))]
    if len(have) < MIN_NEIGHBORS:
        print(f"Станций с данными мало: {len(have)}. Для демо нужно хотя бы "
              f"{MIN_NEIGHBORS} соседних станции кроме MAJO.")
        return 1
    if no_data:
        print(f"Внимание: для {', '.join(no_data)} данных нет, демо пойдёт "
              f"без них ({len(have) + 1} станции вместе с MAJO).")
    print("Записи готовы.")
    return 0


def no_internet(detail):
    print("Не удалось скачать записи станций с сервера EarthScope.")
    print("Проверьте подключение к интернету и запустите демо ещё раз.")
    print(f"(Техническая причина: {detail})")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nПрервано.")
        sys.exit(1)
    except Exception as err:  # just in case: no tracebacks for the user
        print(f"Неожиданная ошибка при подготовке демо: {err}")
        sys.exit(1)
