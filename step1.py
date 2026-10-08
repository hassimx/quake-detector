"""step 1: read the built-in obspy example and draw a plot"""

import matplotlib

# "Agg" is a mode without a window: the plot is saved to a file
# This way the script works on a server without a screen. On your own computer
# you can remove this line, and the plot will open in a window
matplotlib.use("Agg")

from obspy import read

# read() without arguments returns the obspy demo data:
# a Stream of three traces: components EHZ, EHN, EHE
st = read()

# print short info: network, station, channel, time, number of samples
print(st)

# details about the first trace (Trace): sampling rate etc
tr = st[0]
print()
print("Станция:", tr.stats.station)
print("Канал:", tr.stats.channel)
print("Частота дискретизации (Гц):", tr.stats.sampling_rate)
print("Количество отсчётов:", tr.stats.npts)
print("Начало записи:", tr.stats.starttime)

# draw all three traces one under another and save to PNG
st.plot(outfile="step1.png")
print()
print("График сохранён в step1.png")
