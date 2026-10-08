"""find earthquakes of M5-6 in central Japan using the USGS catalog"""

from obspy import UTCDateTime
from obspy.clients.fdsn import Client

client = Client("USGS")

events = client.get_events(
    starttime=UTCDateTime("2015-01-01"),
    endtime=UTCDateTime("2024-12-31"),
    minmagnitude=5.0,
    maxmagnitude=6.0,
    minlatitude=33, maxlatitude=40,
    minlongitude=136, maxlongitude=143,
    orderby="magnitude",
    limit=15,
)

for ev in events:
    origin = ev.origins[0]
    mag = ev.magnitudes[0].mag
    depth = round(origin.depth / 1000) if origin.depth else "?"
    place = ev.event_descriptions[0].text if ev.event_descriptions else ""
    print(origin.time, f"M{mag}", f"глубина {depth} км", place)