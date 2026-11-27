"""Status and reason vocabularies shared by every document job table.

Kept apart from ``document_jobs`` because the models need them for CHECK
constraints and that module imports the tenant middleware, which imports the models.
"""

JOB_STATUSES = ("queued", "running", "succeeded", "partial", "failed")
ACTIVE_STATUSES = ("queued", "running")
RUN_JOB_REASONS = ("deadline_exceeded", "interrupted", "internal_error")
