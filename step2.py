"""step 2: download the record of the Tohoku earthquake (11 March 2011)
from station IU.MAJO (Matsushiro, Japan), channel BHZ, and save it."""

import matplotlib

matplotlib.use("Agg")  # draw to a file, without a window

from obspy import UTCDateTime
from obspy.clients.fdsn import Client

# start of the record: 11 March 2011, 05:30 UTC. The shock itself started around 05:46 UTC,
# so the window has some silence before the earthquake
start = UTCDateTime("2011-03-11T05:30:00")
end = start + 60 * 60  # exactly one hour (in seconds)

# the client is the server where seismic data is stored. It used to be called IRIS,
# now EarthScope (same address, "IRIS" in obspy is considered an outdated name)
client = Client("EARTHSCOPE")

# location code is part of the sensor name. First we try "00",
# and if there is no such record, we take any ("*")
try:
    st = client.get_waveforms("IU", "MAJO", "00", "BHZ", start, end)
except Exception as err:
    print(f'Не вышло с location "00" ({err}), пробуем "*"')
    st = client.get_waveforms("IU", "MAJO", "*", "BHZ", start, end)

print(st)

# save in MiniSEED format, the standard format for seismic records
st.write("tohoku_2011.mseed", format="MSEED")
print("Данные сохранены в tohoku_2011.mseed")

# plot
st.plot(outfile="tohoku_2011.png")
print("График сохранён в tohoku_2011.png")
