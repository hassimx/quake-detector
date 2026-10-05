"""Шаг 10: ищем возможные причины "ложных" тревог в каталоге USGS.

Идея: детектор сработал в тихие часы. Смотрим, не было ли в это время
настоящего землетрясения, которое датчик мог поймать:
  а) рядом с Японией (слабое, но близкое);
  б) где угодно в мире, но сильное (M 5.5+): такие волны доходят
     через всю Землю.

Алгоритм детектора здесь НЕ трогаем: скрипт только читает каталог.
Весь вывод печатается на экран и сохраняется в results/step10_output.txt.
"""

import os

from obspy import UTCDateTime
from obspy.clients.fdsn import Client
from obspy.clients.fdsn.header import FDSNNoDataException

# Времена тревог (UTC), которые мы проверяем.
ALARMS = [
    "2019-09-20T10:48:45",
    "2019-09-20T10:50:00",
    "2019-09-20T10:54:09",
    "2018-03-12T02:45:07",
]

# Рамка "рядом с Японией" (градусы) и минимальная магнитуда.
JAPAN_BOX = dict(minlatitude=30, maxlatitude=45,
                 minlongitude=130, maxlongitude=146)
JAPAN_MIN_MAG = 2.0
JAPAN_BEFORE = 300   # секунд до тревоги
JAPAN_AFTER = 30     # секунд после тревоги

# Сильные землетрясения по всему миру.
WORLD_MIN_MAG = 5.5
WORLD_BEFORE = 30 * 60  # 30 минут до тревоги

OUTPUT_FILE = os.path.join("results", "step10_output.txt")


class Report:
    """Печатает строку на экран и одновременно пишет её в файл."""

    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.file = open(path, "w", encoding="utf-8")

    def line(self, text=""):
        print(text)
        self.file.write(text + "\n")
        self.file.flush()  # чтобы при сбое уже напечатанное сохранилось

    def close(self):
        self.file.close()


def find_events(client, **kwargs):
    """Запрашивает каталог. Возвращает (список событий, текст ошибки).

    Если каталог честно отвечает "ничего нет" — это пустой список и
    ошибки нет. Если запрос сломался (например, нет интернета) — это
    ошибка: её нельзя путать с "ничего не найдено".
    """
    try:
        catalog = client.get_events(orderby="time", **kwargs)
    except FDSNNoDataException:
        return [], None
    except Exception as err:
        return [], str(err)
    return list(catalog), None


def describe(event):
    """Время, магнитуда и место события одной строкой."""
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
    """Печатает результат одного запроса."""
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

        # а) рядом с Японией, слабые тоже: от -300 с до +30 с
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

        # б) весь мир, только сильные: за 30 минут до тревоги
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
