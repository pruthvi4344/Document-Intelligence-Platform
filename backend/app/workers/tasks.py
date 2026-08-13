from app.workers.celery_app import celery_app

# Ingestion task (parse -> chunk -> embed -> store -> summarize) implemented in Phase 2,
# triggered by POST /api/documents/upload (Section 6).


@celery_app.task(name="health.ping")
def ping() -> str:
    return "pong"
