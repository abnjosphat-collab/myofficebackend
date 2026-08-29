# app/main.py — compatibility shim. The real entrypoint is backend/main.py
# (top-level, not inside the app package); this file exists only so
# `uvicorn app.main:app` also works, since that's the invocation Uvicorn's
# own quickstart/most tutorials teach and it's been run by mistake here more
# than once (docs: run from backend/, `uvicorn main:app --reload`).
from main import app

__all__ = ["app"]
