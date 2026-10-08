"""helper for run_demo.bat: waits for the server or checks that the port is free.

    .venv\\Scripts\\python -m server.wait_server 90      wait for the server to answer, up to 90 s
    .venv\\Scripts\\python -m server.wait_server --free  make sure there is no server yet
Exit code: 0 - all fine, 1 - not (the message in Russian has already been printed).
"""

import sys
import time
import urllib.request

URL = "http://127.0.0.1:8000/api/config"


def answers():
    """True if something answers at the server address"""
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
