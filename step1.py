"""Шаг 1: читаем встроенный пример obspy и рисуем график."""

import matplotlib

# "Agg" — режим без окна: график сохраняется в файл.
# Так скрипт работает и на сервере без экрана. На своём компьютере
# можешь убрать эту строку, и график откроется в окне.
matplotlib.use("Agg")

from obspy import read

# read() без аргументов возвращает демо-данные obspy:
# Stream (поток) из трёх трасс — компоненты EHZ, EHN, EHE.
st = read()

# Печатаем краткую информацию: сеть, станция, канал, время, число отсчётов.
print(st)

# Подробности про первую трассу (Trace): частота дискретизации и т.д.
tr = st[0]
print()
print("Станция:", tr.stats.station)
print("Канал:", tr.stats.channel)
print("Частота дискретизации (Гц):", tr.stats.sampling_rate)
print("Количество отсчётов:", tr.stats.npts)
print("Начало записи:", tr.stats.starttime)

# Рисуем все три трассы друг под другом и сохраняем в PNG.
st.plot(outfile="step1.png")
print()
print("График сохранён в step1.png")
