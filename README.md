# Employee Attendance & Analytics API

FastAPI + MongoDB service for employee attendance: punch in/out, manual corrections with an audit trail, and analytics (employee monthly summary, department summary, late leaderboard, department trend).

## Run

    python -m venv .venv
    .venv\Scripts\activate          (Windows)   or   source .venv/bin/activate
    pip install -r requirements.txt
    uvicorn app.main:app --port 8000

Needs Python 3.11+ and MongoDB 6.0+ (for example `docker run -p 27017:27017 mongo:7`).

## Configuration

- `MONGO_URI` (default `mongodb://localhost:27017`)
- `MONGO_DB` (default `attendance_db`)

Real environment variables take priority over a local `.env`. Indexes are created at startup and are safe to re-run.

## Notes

- Dates are IST; times in the API are epoch milliseconds.
- `GET /admin/explain/{endpoint}` runs MongoDB `explain` on the real query behind an endpoint.
- Design choices are in `DECISIONS.md`; the review of the starter code is in `REVIEW.md`.