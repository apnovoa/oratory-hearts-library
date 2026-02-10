# Gunicorn configuration for Bibliotheca
#
# The lending checkout lock is process-local (threading.Lock), so this
# application MUST run with a single worker to prevent double-checkouts.

workers = 1
bind = "0.0.0.0:8080"


def post_fork(server, worker):
    server.log.info("Worker spawned (pid: %s). Single-worker mode active.", worker.pid)
