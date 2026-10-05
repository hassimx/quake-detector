"""Вспомогательное для run_demo.bat: ждём сервер или проверяем, что порт свободен.

    .venv\\Scripts\\python -m server.wait_server 90      ждать ответа сервера до 90 с
    .venv\\Scripts\\python -m server.wait_server --free  убедиться, что сервера ещё нет
Код возврата: 0 - всё в порядке, 1 - нет (сообщение по-русски уже напечатано).
"""

import sys
import time
import urllib.request

URL = "http://127.0.0.1:8000/api/config"


def answers():
    """True, если по адресу сервера кто-то отвечает."""
    try:
        urllib.request.urlopen(URL, timeout=2)
        return True
    except Exception:
        return False


def main(argv):
    if argv[:1] == ["--free"]:
        if answers():
            print("Порт 8000 уже занят: похоже, сервер от прошлого запуска ещё "
                  "работает.")
            print("Закройте его окно (Quake Detector SERVER) и запустите демо снова.")
            return 1
        return 0
    limit = float(argv[0]) if argv else 60
    deadline = time.time() + limit
    while time.time() < deadline:
        if answers():
            return 0
        time.sleep(1)
    print(f"Сервер не ответил за {limit:g} секунд. Посмотрите окно сервера: "
          "там должна быть причина.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
