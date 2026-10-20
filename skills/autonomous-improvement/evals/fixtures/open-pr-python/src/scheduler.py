def should_run(job):
    return job.retries < 3 and job.enabled


def schedule(jobs):
    return [job for job in jobs if should_run(job)]
