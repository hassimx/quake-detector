"""Проверки подготовки демо (prepare_demo, wait_server) без интернета."""

import pytest

from server import prepare_demo, wait_server
from step12 import NEIGHBORS
from step13 import cache_path


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Пустая папка проекта: только запись MAJO, как в репозитории."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / prepare_demo.MAJO_FILE).write_bytes(b"x")
    return tmp_path


def make_station_files():
    for key in NEIGHBORS:
        with open(cache_path(key, prepare_demo.TAG), "wb") as f:
            f.write(b"x")


def test_nothing_downloaded_when_files_exist(project, monkeypatch, capsys):
    (project / "data").mkdir()
    make_station_files()

    def no_client(*a, **k):
        raise AssertionError("скачивание не должно запускаться")
    monkeypatch.setattr(prepare_demo, "Client", no_client)
    assert prepare_demo.main() == 0
    assert "скачивать ничего не нужно" in capsys.readouterr().out


def test_missing_majo_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert prepare_demo.main() == 1
    assert prepare_demo.MAJO_FILE in capsys.readouterr().out


def test_no_internet_message_not_traceback(project, monkeypatch, capsys):
    def broken(*a, **k):
        raise ConnectionError("нет сети")
    monkeypatch.setattr(prepare_demo, "Client", broken)
    assert prepare_demo.main() == 1
    out = capsys.readouterr().out
    assert "интернет" in out and "Traceback" not in out


def test_download_error_message(project, monkeypatch, capsys):
    monkeypatch.setattr(prepare_demo, "Client", lambda *a, **k: object())
    monkeypatch.setattr(prepare_demo, "load_neighbor",
                        lambda *a, **k: (None, "ОШИБКА ЗАГРУЗКИ: обрыв"))
    assert prepare_demo.main() == 1
    assert "интернет" in capsys.readouterr().out


def test_too_few_stations_with_data(project, monkeypatch, capsys):
    monkeypatch.setattr(prepare_demo, "Client", lambda *a, **k: object())
    monkeypatch.setattr(prepare_demo, "load_neighbor",
                        lambda *a, **k: (None, "нет данных"))
    assert prepare_demo.main() == 1
    assert "мало" in capsys.readouterr().out


def test_wait_server_free_and_busy(monkeypatch, capsys):
    monkeypatch.setattr(wait_server, "answers", lambda: False)
    assert wait_server.main(["--free"]) == 0
    assert wait_server.main(["1"]) == 1  # сервер так и не ответил
    monkeypatch.setattr(wait_server, "answers", lambda: True)
    assert wait_server.main(["--free"]) == 1
    assert "8000" in capsys.readouterr().out
    assert wait_server.main(["5"]) == 0
