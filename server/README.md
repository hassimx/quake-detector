# Бэкенд quake-detector

Сервер принимает потоки со станций (пока их заменяет воспроизведение записанных
файлов), на каждой станции запускает тот же детектор STA/LTA, что в step11
(порог 10), и применяет правило сети: тревоги разных станций в пределах ±60 с
образуют событие; событие «подтверждено», если в нём тревоги не меньше
`QUAKE_MIN_STATIONS` разных станций (по умолчанию 2). Данные и тревоги лежат в
SQLite (`server/quake.db`, в git не попадает).

## Запуск

    .venv/bin/pip install -r requirements-server.txt
    .venv/bin/uvicorn server.app:create_app --factory --port 8000
    # в другом окне (нужны файлы data/, их создаёт step13.py):
    .venv/bin/python -m server.replay quake:2021-10-07 --speed 60
    .venv/bin/python -m server.replay hour:2016-06-11T17 --speed 0

## API

| Запрос | Что отдаёт |
|---|---|
| `POST /api/ingest/{станция}` | принимает кусок записи, возвращает новые тревоги |
| `GET /api/stations` | станции с координатами (для карты) |
| `GET /api/alarms` | тревоги (`?station=`, `?limit=`) |
| `GET /api/events` | события с тревогами (`?status=confirmed`) |
| `GET /api/waveform` | запись станции за интервал (`station`, `start`, `end`) |
| `GET /api/config` | текущие настройки правила |

Тесты: `.venv/bin/python -m pytest server -q`.
