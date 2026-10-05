"""Шаг 2: скачиваем запись землетрясения Тохоку (11 марта 2011)
со станции IU.MAJO (Мацусиро, Япония), канал BHZ, и сохраняем её."""

import matplotlib

matplotlib.use("Agg")  # рисуем в файл, без окна

from obspy import UTCDateTime
from obspy.clients.fdsn import Client

# Начало записи: 11 марта 2011, 05:30 UTC. Сам толчок начался около 05:46 UTC,
# поэтому в окне будет немного тишины до землетрясения.
start = UTCDateTime("2011-03-11T05:30:00")
end = start + 60 * 60  # ровно один час (в секундах)

# Клиент — это сервер, где хранятся сейсмоданные. Раньше он назывался IRIS,
# теперь EarthScope (адрес тот же, "IRIS" в obspy считается устаревшим именем).
client = Client("EARTHSCOPE")

# location code — часть названия датчика. Сначала пробуем "00",
# а если такой записи нет, берём любой ("*").
try:
    st = client.get_waveforms("IU", "MAJO", "00", "BHZ", start, end)
except Exception as err:
    print(f'Не вышло с location "00" ({err}), пробуем "*"')
    st = client.get_waveforms("IU", "MAJO", "*", "BHZ", start, end)

print(st)

# Сохраняем в формате MiniSEED — стандартный формат сейсмозаписей.
st.write("tohoku_2011.mseed", format="MSEED")
print("Данные сохранены в tohoku_2011.mseed")

# График.
st.plot(outfile="tohoku_2011.png")
print("График сохранён в tohoku_2011.png")
