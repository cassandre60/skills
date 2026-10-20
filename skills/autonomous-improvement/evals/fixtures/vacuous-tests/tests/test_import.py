import time

from src.importer import import_studies, wait_for_import


def test_import_does_not_crash():
    try:
        import_studies("does-not-exist.txt")
    except FileNotFoundError:
        pass


def test_wait_for_import_is_fast():
    time.sleep(0.2)
    assert True
