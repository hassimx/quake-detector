"""network rule: alarms from different stations close in time = one event"""

from step11 import MATCH_SEC


def link_alarm(db, alarm_id, station, time_ts, min_stations):
    """attaches a new alarm to an event; returns the event id or None.

    We take alarms of OTHER stations within +-60 s (MATCH_SEC, as in
    step11-13). If there are any, the alarms together form an event. The event
    is "confirmed" when it has alarms from at least min_stations different
    stations; until then it stays a "candidate". An alarm without a pair stays
    without an event: this is how "at least two stations" filters out single
    alarms. The order of arrival does not matter: we look for neighbors in both
    directions in time.
    """
    others = db.alarms_near(time_ts - MATCH_SEC, time_ts + MATCH_SEC, station)
    if not others:
        return None
    old = sorted({o["event_id"] for o in others if o["event_id"] is not None})
    if old:
        event_id = old[0]
        db.merge_events(old[1:], event_id)  # a chain joined several events
    else:
        event_id = db.create_event()
    db.assign([alarm_id] + [o["id"] for o in others], event_id)
    db.refresh_event(event_id, min_stations)
    return event_id
