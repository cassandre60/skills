import time


def import_studies(path):
    with open(path, "r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def wait_for_import(path):
    time.sleep(0.2)
    return import_studies(path)
