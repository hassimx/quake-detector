"""step 10: look for possible causes of the "false" alarms in the USGS catalog.

Idea: the detector fired in quiet hours. We check whether there was a real
earthquake at that time that the sensor could have caught:
  a) near Japan (weak, but close);
  b) anywhere in the world, but strong (M 5.5+): such waves travel
     through the whole Earth.

We do NOT touch the detector algorithm here: the script only reads the catalog.
All output is printed to the screen and saved to results/step10_output.txt.
"""

import os

from obspy import UTCDateTime
from obspy.clients.fdsn import Client
from obspy.clients.fdsn.header import FDSNNoDataException

# alarm times (UTC) that we check
ALARMS = [
    "2019-09-20T10:48:45",
    "2019-09-20T10:50:00",
    "2019-09-20T10:54:09",
    "2018-03-12T02:45:07",
]

# the "near Japan" box (degrees) and the minimum magnitude
JAPAN_BOX = dict(minlatitude=30, maxlatitude=45,
                 minlongitude=130, maxlongitude=146)
JAPAN_MIN_MAG = 2.0
JAPAN_BEFORE = 300   # seconds before the alarm
JAPAN_AFTER = 30     # seconds after the alarm

# strong earthquakes all over the world
WORLD_MIN_MAG = 5.5
WORLD_BEFORE = 30 * 60  # 30 minutes before the alarm

OUTPUT_FILE = os.path.join("results", "step10_output.txt")


class Report:
    """prints a line to the screen and writes it to the file at the same time"""

    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.file = open(path, "w", encoding="utf-8")

    def line(self, text=""):
        print(text)
        self.file.write(text + "\n")
        self.file.flush()  # so that what was already printed is saved after a crash

    def close(self):
        self.file.close()


def find_events(client, **kwargs):
    """queries the catalog. Returns (list of events, error text).

    If the catalog honestly answers "nothing" it is an empty list and
    no error. If the request broke (for example, no internet) it is an
    error: it must not be confused with "nothing found".
    """
    try:
        catalog = client.get_events(orderby="time", **kwargs)
    except FDSNNoDataException:
        return [], None
    except Exception as err:
        return [], str(err)
    return list(catalog), None


def describe(event):
    """time, magnitude and place of an event in one line"""
    origin = event.preferred_origin() or event.origins[0]
    magnitude = event.preferred_magnitude() or (
        event.magnitudes[0] if event.magnitudes else None)
    if magnitude is not None:
        mag_text = f"M {magnitude.mag} ({magnitude.magnitude_type})"
    else:
        mag_text = "M ?"
    place = event.event_descriptions[0].text if event.event_descriptions \
        else "место не указано"
    return f"{origin.time}  {mag_text}  {place}"


def show(report, title, events, error):
    """prints the result of one query"""
    report.line(f"  {title}")
    if error:
        report.line(f"    ОШИБКА ЗАПРОСА (это не 'ничего нет'): {error}")
    elif not events:
        report.line("    ничего не найдено")
    else:
        for event in events:
            report.line(f"    {describe(event)}")


def main():
    report = Report(OUTPUT_FILE)
    client = Client("USGS", timeout=60)
    errors = 0

    for alarm_text in ALARMS:
        alarm = UTCDateTime(alarm_text)
        report.line(f"=== Тревога {alarm} ===")

        # a) near Japan, weak ones too: from -300 s to +30 s
        events, error = find_events(
            client,
            starttime=alarm - JAPAN_BEFORE,
            endtime=alarm + JAPAN_AFTER,
            minmagnitude=JAPAN_MIN_MAG,
            **JAPAN_BOX,
        )
        errors += error is not None
        show(report, f"а) Япония, M {JAPAN_MIN_MAG}+, "
                     f"-{JAPAN_BEFORE} с ... +{JAPAN_AFTER} с:", events, error)

        # b) the whole world, only strong ones: 30 minutes before the alarm
        events, error = find_events(
            client,
            starttime=alarm - WORLD_BEFORE,
            endtime=alarm,
            minmagnitude=WORLD_MIN_MAG,
        )
        errors += error is not None
        show(report, f"б) весь мир, M {WORLD_MIN_MAG}+, "
                     f"за {WORLD_BEFORE // 60} мин до тревоги:", events, error)
        report.line()

    if errors:
        report.line(f"ВНИМАНИЕ: запросов с ошибкой: {errors}. "
                    "Результаты неполные, запусти скрипт ещё раз.")
    report.line(f"Вывод сохранён в {OUTPUT_FILE}")
    report.close()


if __name__ == "__main__":
    main()
