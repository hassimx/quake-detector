"""Правило сети: тревоги разных станций рядом по времени = одно событие."""

from step11 import MATCH_SEC


def link_alarm(db, alarm_id, station, time_ts, min_stations):
    """Привязывает новую тревогу к событию; возвращает id события или None.

    Берём тревоги ДРУГИХ станций в пределах +-60 сек (MATCH_SEC, как в
    step11-13). Если такие есть, тревоги вместе образуют событие. Событие
    "подтверждено", когда в нём тревоги не меньше min_stations разных станций;
    пока станций меньше, оно остаётся "кандидатом". Тревога без пары остаётся
    без события: так "минимум две станции" отсеивает одиночные тревоги.
    Порядок прихода не важен: ищем соседей в обе стороны по времени.
    """
    others = db.alarms_near(time_ts - MATCH_SEC, time_ts + MATCH_SEC, station)
    if not others:
        return None
    old = sorted({o["event_id"] for o in others if o["event_id"] is not None})
    if old:
        event_id = old[0]
        db.merge_events(old[1:], event_id)  # цепочка связала несколько событий
    else:
        event_id = db.create_event()
    db.assign([alarm_id] + [o["id"] for o in others], event_id)
    db.refresh_event(event_id, min_stations)
    return event_id
