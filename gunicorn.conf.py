# Gunicorn configuration for Bibliotheca
#
# The lending checkout lock is process-local (threading.Lock), so this
# application MUST run with a single worker to prevent double-checkouts.
import os

workers = 1
bind = "0.0.0.0:8080"
timeout = int(os.environ.get("GUNICORN_TIMEOUT_SECONDS", "120"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT_SECONDS", "30"))


def post_fork(server, worker):
    server.log.info("Worker spawned (pid: %s). Single-worker mode active.", worker.pid)
