# REVIEW.md

Review of the starter `app/main.py`. Where = function in the starter.

| # | Where | What is wrong | How you would notice it | How I fixed it |
|---|---|---|---|---|
| 1 | compute_late_minutes | Shift start is built from the punch-in's own date and timezone, not IST. A punch-in stored in UTC is compared with the wrong clock time, and an overnight shift (22:00) is compared against the wrong day. | Punch in at 09:40 IST for a 09:30 shift using a UTC time; or a 00:10 punch-in on a 22:00 shift. | Convert to IST first and compare against the shift start of the attendance day. |
| 2 | compute_late_minutes | Grace check uses `int(seconds / 60) > 10`. 10 min 1 s becomes 10, which is not > 10, so the person is not late. The rule is "strictly more than 10:00 is late". | Punch in at 09:40:01 for a 09:30 shift: returns 0, should be 10. | Cut to whole seconds, treat late only if seconds > 600, return whole minutes rounded down. |
| 3 | compute_work_hours | Python `round()` rounds half to even, and works on floats. 3.125 becomes 3.12 instead of 3.13. | 11250 seconds of work. | Use Decimal on whole seconds with ROUND_HALF_UP. |
| 4 | compute_overtime | No 30-minute minimum. 29 minutes of overtime is returned as 29. | Punch out 29 minutes after shift end. | Return 0 unless overtime is at least 30 minutes. |
| 5 | compute_overtime | Shift end is always placed on the attendance date. For an overnight shift (22:00 to 06:00) the end is really the next morning. | Overnight shift, punch out at 07:00 next day. | If shift end is not later than shift start, move shift end to the next day. |
| 6 | EmployeeIn | No validation at all: emp_code format, name and department length, email shape, HH:MM times, joined_on date. shift_start equal to shift_end is also accepted. | POST with bad values returns 201. | Added validators; invalid input returns 422. |
| 7 | create_employee | Check-then-insert (find_one then insert_one) and no unique index. Two parallel requests with the same emp_code can both succeed. | Send two identical POST /employees at the same time. | Unique index on emp_code, created at startup. A duplicate key error becomes 409. |
| 8 | create_employee | created_at is `datetime.now()`: naive local time, returned as an ISO string. The contract wants epoch milliseconds in UTC. | Look at the 201 response. | Store and return created_at as epoch milliseconds from a UTC time. |
| 9 | list_employees | `skip = page * page_size` skips the first page. Page 1 returns the second page of results. | GET /employees?page=1 with more than 20 employees. | `skip = (page - 1) * page_size`. |
| 10 | list_employees | `total` counts all employees and ignores the department filter. | GET /employees?department=X, compare total with items. | Count with the same filter as the query. |
| 11 | list_employees | No sort order, so pages can overlap or repeat. No check on page or page_size. | Page through results; try page_size=1000. | Sort by emp_code ascending; page at least 1; page_size from 1 to 100, else 422. |
| 12 | punch_in | If the employee does not exist, `emp` is None and `emp["shift_start"]` crashes with a 500. | Punch in with an unknown emp_code. | Return 404 when the employee is not found. |
| 13 | punch_in | `datetime.fromtimestamp(ms / 1000)` uses the server's local time and gives a naive value. The date is also taken in server time, not IST, so the attendance day can be wrong. | Punch in near midnight IST on a server not in IST. | Convert epoch ms to a UTC-aware time, cut to whole seconds, and decide the attendance day with the `attendance_date()` helper (uses the shift times, in IST). |
| 14 | punch_in | Check-then-insert with no unique (emp_code, date) index. Two parallel punch-ins can both succeed and create two records for one day. | Send two identical punch-in requests at the same time. | Unique index on (emp_code, date) at startup. The loser gets a duplicate key error and 409. |
| 15 | punch_in | status accepts any string. The response returns datetimes and an extra `id` field instead of the contract's epoch-millisecond fields. | Punch in with status "BANANA"; look at the response. | status limited to PRESENT, WFH, ON_DUTY; punched_at must be a strict integer in a sane epoch-ms range; response returns the contract's fields with times as epoch ms. |
| 16 | list_attendance | Loads every matching document into Python, sorts and slices there. Slow and memory hungry at 100k records. Sorts only by date, so ties are unstable. | GET /attendance with no filters on a big dataset. | Sort, skip and limit in MongoDB (date descending, emp_code ascending), backed by an index. |
| 17 | list_attendance | No validation of date_from, date_to, status or paging. date_from after date_to returns an empty list instead of 422. | date_from=2026-10-15&date_to=2026-10-10. | Validate formats, range order, status and paging; return 422. |
| 18 | health | Always returns ok even when MongoDB is down. | Stop Mongo and call /health. | Ping MongoDB; return 503 if it fails. |
| 19 | module setup | No indexes are created anywhere, but the contract requires indexes created at startup, idempotently. | Explain shows COLLSCAN on every query. | Create all indexes at startup with create_index, which is safe to repeat. |
| 20 | MongoClient | Created without tz_aware, so PyMongo returns naive datetimes that the code then treats as local time. | Compare stored punch_in with the value read back. | MongoClient(..., tz_aware=True); all times are UTC-aware. |

## Looked at and decided it is not a defect

- `load_dotenv()` does not override variables that are already set, so real environment variables already take priority over `.env`, as the contract requires.
- `int(seconds / 60)` in overtime rounds toward zero, and `max(0, ...)` removes negatives, so it matches "round down" for the values that can occur.
- `doc.pop("_id")` after `insert_one` is needed, because PyMongo adds `_id` to the dict it inserts, and that field must not leak into the response.
- The 409 on a duplicate emp_code is correct behaviour. The problem was only that it was not safe under concurrency (row 7).