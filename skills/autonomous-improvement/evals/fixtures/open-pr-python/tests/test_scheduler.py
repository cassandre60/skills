from src.scheduler import schedule


class Job:
    def __init__(self, retries, enabled):
        self.retries = retries
        self.enabled = enabled


def test_schedule_keeps_enabled_jobs_with_retries_left():
    jobs = [Job(retries=0, enabled=True), Job(retries=3, enabled=True)]
    assert schedule(jobs) == [jobs[0]]
