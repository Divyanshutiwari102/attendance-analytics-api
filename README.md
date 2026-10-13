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

## Endpoints

- `GET /health`
- `POST /employees`, `GET /employees`
- `POST /attendance/punch-in`, `POST /attendance/punch-out`
- `GET /attendance`
- `PATCH /attendance/{emp_code}/{date}` (manual correction, saved in the record's history)
- `GET /analytics/employees/{emp_code}/monthly`
- `GET /analytics/departments/summary`
- `GET /analytics/leaderboard/late`
- `GET /analytics/departments/{department}/trend`
- `GET /admin/explain/{endpoint}` (MongoDB `explain` for the real query behind an endpoint)

## Notes

- Dates are IST; times in the API are epoch milliseconds.
- Duplicate punch-ins and duplicate employees are stopped by unique indexes, so parallel requests are safe.
- Design choices are in `DECISIONS.md`; the review of the starter code is in `REVIEW.md`.