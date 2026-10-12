"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator, StrictInt
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
import random
import time
import calendar
import re
from contextlib import asynccontextmanager

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), tz_aware=True)
db = client[os.getenv("MONGO_DB", "attendance_db")]
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    db.employees.create_index([("emp_code", 1)], unique=True)
    db.employees.create_index([("department", 1), ("emp_code", 1)])
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])
    db.attendance_logs.create_index([("emp_code", 1), ("date", -1)])
    db.attendance_logs.create_index([("emp_code", 1), ("punch_in", -1)])
    yield

IST = timezone(timedelta(hours=5, minutes=30))


def ms_to_dt(ms: int) -> datetime:
    """Convert epoch milliseconds to UTC datetime."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def dt_to_ms(dt: datetime) -> int:
    """Convert datetime to epoch milliseconds."""
    return int(dt.timestamp() * 1000)


def to_ist(dt: datetime) -> datetime:
    """Convert datetime to IST timezone."""
    if dt.tzinfo is None:
        # Assume UTC if no timezone info
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(punch_in: datetime, shift_start: str, shift_end: str) -> int:
    """
    Compute late minutes based on shift start time.
    R1: Truncate instants to whole seconds before calculation.
    R2: Late only if strictly more than 10:00 after shift_start.
        When late, late_minutes = whole minutes (floor) elapsed since shift_start.
    Handles overnight shifts.
    """
    # Truncate to whole seconds (R1)
    punch_in = punch_in.replace(microsecond=0)

    # Ensure punch_in is in IST for comparison
    punch_in_ist = to_ist(punch_in)

    # Parse shift times
    start_h, start_m = map(int, shift_start.split(":"))
    end_h, end_m = map(int, shift_end.split(":"))

    # Determine if this is an overnight shift (shift ends on next calendar day)
    is_overnight_shift = (end_h * 60 + end_m) <= (start_h * 60 + start_m)

    # Calculate the date of the shift start for this punch_in
    # For overnight shifts, if punch_in time is earlier than shift_end, the shift started previous day
    if is_overnight_shift and punch_in_ist.time() < datetime.min.time().replace(hour=end_h, minute=end_m):
        # Shift started previous day
        shift_start_date = punch_in_ist.date() - timedelta(days=1)
    else:
        # Shift started same day as punch_in (or not overnight)
        shift_start_date = punch_in_ist.date()

    # Create shift start datetime in IST
    start = datetime(shift_start_date.year, shift_start_date.month, shift_start_date.day,
                     start_h, start_m, 0, 0, tzinfo=IST)

    # Calculate late duration in seconds
    late_seconds = (punch_in_ist - start).total_seconds()

    # R2: Late only if strictly more than 10:00 after shift_start
    if late_seconds > 10 * 60:  # More than 10 minutes
        # Return whole minutes (floor) elapsed since shift_start
        return int(late_seconds // 60)
    else:
        return 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    """
    Compute work hours between punch-in and punch-out.
    R4: Rounded to 2 decimals half-up (not banker's rounding).
    """
    # Truncate to whole seconds (R1 applies to instants before calculation)
    punch_in = punch_in.replace(microsecond=0)
    punch_out = punch_out.replace(microsecond=0)

    total_seconds = int((punch_out - punch_in).total_seconds())
    # Use Decimal for half-up rounding to 2 decimal places
    hours = Decimal(total_seconds) / Decimal(3600)
    rounded_hours = hours.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return float(rounded_hours)


def compute_overtime(punch_out: datetime, shift_start: str, shift_end: str, date_str: str) -> int:
    """
    Compute overtime minutes based on shift end time.
    R1: Truncate instants to whole seconds before calculation.
    R3: Overtime minutes = whole minutes (floor) between shift_end and punch_out,
        counted only when that is >= 30; otherwise 0.
    Handles overnight shifts where shift_end falls on the next calendar day.
    """
    # Truncate to whole seconds (R1)
    punch_out = punch_out.replace(microsecond=0)

    # Convert date_str to IST datetime at midnight of that date
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"Invalid date format: {date_str}. Expected YYYY-MM-DD")

    # Create IST datetime at midnight of the given date
    date_midnight_ist = date_obj.replace(tzinfo=IST)

    # Parse shift times
    start_h, start_m = map(int, shift_start.split(":"))
    end_h, end_m = map(int, shift_end.split(":"))

    # Determine if this is an overnight shift (shift ends on next calendar day)
    is_overnight_shift = (end_h * 60 + end_m) <= (start_h * 60 + start_m)

    # Calculate shift start datetime
    shift_start_dt = date_midnight_ist.replace(hour=start_h, minute=start_m, second=0, microsecond=0)

    # Calculate shift end datetime
    if is_overnight_shift:
        # For overnight shift, shift end is on the next calendar day
        shift_end_dt = shift_start_dt + timedelta(hours=24)  # Add 24 hours to get to same time next day
        shift_end_dt = shift_end_dt.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    else:
        # For non-overnight shift, shift end is on the same calendar day
        shift_end_dt = date_midnight_ist.replace(hour=end_h, minute=end_m, second=0, microsecond=0)

    # Calculate overtime duration in seconds
    overtime_seconds = (punch_out - shift_end_dt).total_seconds()

    # R3: Only count overtime if >= 30 minutes
    if overtime_seconds >= 30 * 60:  # At least 30 minutes
        # Return whole minutes (floor) of overtime
        return int(overtime_seconds // 60)
    else:
        return 0


def validate_iso_date_string(date_str: str) -> str:
    """
    Validate a string is a real calendar date in YYYY-MM-DD format.
    Returns the string if valid, raises ValueError otherwise.
    """
    import re
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        raise ValueError(f"Invalid date format: {date_str}. Expected YYYY-MM-DD")
    try:
        date.fromisoformat(date_str)
    except ValueError:
        raise ValueError(f"Invalid date: {date_str}. Not a real calendar date")
    return date_str


def attendance_date(punch_in_dt: datetime, shift_start: str, shift_end: str) -> str:
    """
    Return the attendance date (YYYY-MM-DD) for a punch-in according to R1.
    R1: Truncate instants to whole seconds before calculation.
    """
    # Truncate to whole seconds
    punch_in_dt = punch_in_dt.replace(microsecond=0)
    # Convert to IST
    punch_in_ist = to_ist(punch_in_dt)
    # Parse shift times
    start_h, start_m = map(int, shift_start.split(":"))
    end_h, end_m = map(int, shift_end.split(":"))
    # Determine if overnight shift
    is_overnight_shift = (end_h * 60 + end_m) <= (start_h * 60 + start_m)
    # If overnight shift and punch_in time is earlier than shift_end, belong to previous day
    if is_overnight_shift and punch_in_ist.time() < datetime.min.time().replace(hour=end_h, minute=end_m):
        attendance_dt = punch_in_ist.date() - timedelta(days=1)
    else:
        attendance_dt = punch_in_ist.date()
    return attendance_dt.isoformat()


def derive_fields(emp, status, punch_in_dt, punch_out_dt, date_str):
    """Return (late_minutes, work_hours, overtime_minutes, half_day) per R2-R5."""
    if status in ("ABSENT", "LEAVE") or punch_in_dt is None:
        return 0, None, 0, False
    # we have a punch_in
    late = compute_late_minutes(punch_in_dt, emp["shift_start"], emp["shift_end"])
    if punch_out_dt is None:
        return late, None, 0, False
    work = compute_work_hours(punch_in_dt, punch_out_dt)
    ot = compute_overtime(punch_out_dt, emp["shift_start"], emp["shift_end"], date_str)
    half = work < 4.5  # work is already rounded to 2 decimals half-up
    return late, work, ot, half


# Helper to compute working days in a month given joined_on
def compute_working_days(joined_on_str: str, year: int, month: int) -> int:
    """Return number of Mon-Fri days in the month, counting from joined_on if joined during the month."""
    # If joined_on is after the month, return 0
    try:
        joined_on = date.fromisoformat(joined_on_str)
    except ValueError:
        # Should not happen if joined_on is valid
        return 0
    # First and last day of the month
    first_day = date(year, month, 1)
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    if joined_on > last_day:
        return 0
    # Start counting from the later of joined_on and first_day
    start = joined_on if joined_on > first_day else first_day
    # Iterate through the days from start to last_day, count Mon-Fri
    days = 0
    current = start
    while current <= last_day:
        if current.weekday() < 5:  # Monday is 0, Friday is 4
            days += 1
        current += timedelta(days=1)
    return days


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str = Field(..., pattern=r'^EMP\d{4,6}$')
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=120, pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
    department: str = Field(..., min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    shift_end: str = Field(default="18:30", pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    joined_on: str
    @field_validator('joined_on')
    @classmethod
    def validate_joined_on(cls, v):
        return validate_iso_date_string(v)


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = Field(None, strict=True, ge=100000000000, le=4102444800000)
    status: str = Field(default="PRESENT", pattern='^(PRESENT|WFH|ON_DUTY)$')


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = Field(None, strict=True, ge=100000000000, le=4102444800000)


class RegularizeRequest(BaseModel):
    status: Optional[str] = Field(None, pattern='^(PRESENT|ABSENT|LEAVE|WFH|ON_DUTY)$')
    punch_in: Optional[int] = Field(None, strict=True, ge=100000000000, le=4102444800000)
    punch_out: Optional[int] = Field(None, strict=True, ge=100000000000, le=4102444800000)
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        # Ping MongoDB to check if it's available
        client.admin.command('ping')
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="Service unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    # Validate shift_start != shift_end (R10 implied from shift times being different)
    if body.shift_start == body.shift_end:
        raise HTTPException(status_code=422, detail="shift_start must differ from shift_end")
    doc = body.model_dump()
    # Set created_at as epoch milliseconds (UTC)
    doc["created_at"] = dt_to_ms(datetime.now(tz=timezone.utc))
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="emp_code already exists")
    doc.pop("_id", None)
    # Convert created_at to epoch milliseconds in response (already done above)
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    # Validate pagination parameters
    if page < 1:
        raise HTTPException(status_code=422, detail="page must be >= 1")
    if page_size < 1 or page_size > 100:
        raise HTTPException(status_code=422, detail="page_size must be between 1 and 100")

    q = {}
    if department:
        q["department"] = department
    skip = (page - 1) * page_size  # Fix pagination offset
    total = db.employees.count_documents(q)  # Fix: count filtered set, not all documents
    items = list(db.employees.find(q, {"_id": 0}).sort("emp_code").skip(skip).limit(page_size))  # Add sort by emp_code ascending
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="emp_code not found")
    ts = ms_to_dt(body.punched_at) if body.punched_at else datetime.now(tz=timezone.utc)
    # Truncate to whole seconds (R1)
    ts = ts.replace(microsecond=0)
    d = attendance_date(ts, emp["shift_start"], emp["shift_end"])
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"], emp["shift_end"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        res = db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="already punched in for this date")
    doc.pop("_id", None)
    # Convert datetime fields to epoch milliseconds for response
    doc["punch_in"] = dt_to_ms(doc["punch_in"])
    return doc


@app.post("/attendance/punch-out", status_code=200)
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="emp_code not found")

    ts = ms_to_dt(body.punched_at) if body.punched_at else datetime.now(tz=timezone.utc)
    # Truncate to whole seconds (R1)
    ts = ts.replace(microsecond=0)

    max_attempts = 15
    for attempt in range(max_attempts):
        # Find the most recent record for this employee with punch_in <= ts
        # This needs to work across midnight for overnight shifts
        current = db.attendance_logs.find_one(
            {
                "emp_code": emp["emp_code"],
                "punch_in": {"$lte": ts}
            },
            sort=[("punch_in", -1)]  # Most recent first
        )

        if current is None:
            raise HTTPException(status_code=404, detail="No punch-in found for this employee")

        # Truncate instants to whole seconds (R1)
        punch_in_dt = current["punch_in"]
        # punch_in_dt is already a datetime from MongoDB, ensure it has no microseconds
        punch_in_dt = punch_in_dt.replace(microsecond=0)

        # If already punched out, return 409
        if current.get("punch_out") is not None:
            raise HTTPException(status_code=409, detail="Record already punched out")

        # Validate punched_at is after punch_in
        if ts <= punch_in_dt:
            raise HTTPException(status_code=422, detail="punched_at must be after punch_in")

        # Validate punched_at is not more than 24 hours after punch_in
        if ts > punch_in_dt + timedelta(hours=24):
            raise HTTPException(status_code=422, detail="punched_at more than 24 hours after punch_in")

        # Compute derived fields
        # For punch-out, we keep the existing status unless it's being changed via regularize
        # But for a pure punch-out, status remains as it was
        new_status = current.get("status")
        new_punch_in = punch_in_dt  # Keep existing punch_in
        new_punch_out = ts          # Set punch_out to the provided time

        # Compute attendance date from the punch_in (this should match the record's date)
        date_str = attendance_date(punch_in_dt, emp["shift_start"], emp["shift_end"])

        late_minutes, work_hours, overtime_minutes, half_day = derive_fields(
            emp, new_status, new_punch_in, new_punch_out, date_str
        )

        # Prepare update document
        update = {
            "punch_out": new_punch_out,
            "work_hours": work_hours,
            "overtime_minutes": overtime_minutes,
            "half_day": half_day,
        }

        # Build match filter: guard on _id and current state of the fields we are about to update
        match_filter = {
            "_id": current["_id"],
            "punch_in": current.get("punch_in"),
            "punch_out": current.get("punch_out"),
        }
        # For history, we can match on length and existence (but punch-out doesn't modify history)
        if "history" in current:
            match_filter["history"] = {"$size": len(current["history"])}
        else:
            match_filter["history"] = {"$exists": False}

        result = db.attendance_logs.update_one(match_filter, {"$set": update})
        if result.modified_count == 1:
            # Success
            updated_doc = db.attendance_logs.find_one({"_id": current["_id"]})
            updated_doc.pop("_id", None)
            if updated_doc.get("punch_in") is not None:
                updated_doc["punch_in"] = dt_to_ms(updated_doc["punch_in"])
            if updated_doc.get("punch_out") is not None:
                updated_doc["punch_out"] = dt_to_ms(updated_doc["punch_out"])
            return updated_doc
        # else retry after a tiny random sleep
        time.sleep(random.uniform(0, 0.02))

    # If we reach here, all attempts failed
    raise HTTPException(status_code=409, detail="Could not update record due to conflict")


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    # Validate pagination parameters
    if page < 1:
        raise HTTPException(status_code=422, detail="page must be >= 1")
    if page_size < 1 or page_size > 100:
        raise HTTPException(status_code=422, detail="page_size must be between 1 and 100")

    # Validate date_from and date_to are valid dates
    if date_from is not None:
        try:
            validate_iso_date_string(date_from)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    if date_to is not None:
        try:
            validate_iso_date_string(date_to)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    # Validate date_from > date_to
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from cannot be after date_to")

    # Validate status if provided
    if status and status not in ["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]:
        raise HTTPException(status_code=422, detail="invalid status value")

    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status

    # Get total count
    total = db.attendance_logs.count_documents(q)

    # Get paginated and sorted results from MongoDB
    # Sort by date descending, then emp_code ascending
    cursor = db.attendance_logs.find(q).sort([("date", -1), ("emp_code", 1)]).skip((page - 1) * page_size).limit(page_size)

    # Process documents for response
    items = []
    for doc in cursor:
        # Convert MongoDB _id to string and remove it (not exposed in API)
        doc.pop("_id", None)

        # Ensure required fields with defaults
        if "history" not in doc:
            doc["history"] = []
        if "half_day" not in doc:
            doc["half_day"] = False

        # Convert datetime fields to epoch milliseconds
        if "punch_in" in doc and doc["punch_in"] is not None:
            doc["punch_in"] = dt_to_ms(doc["punch_in"])
        if "punch_out" in doc and doc["punch_out"] is not None:
            doc["punch_out"] = dt_to_ms(doc["punch_out"])
        # Note: created_at is not in attendance records, but if it were, we'd convert it too

        items.append(doc)

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeRequest):
    # Validate date format
    try:
        date = validate_iso_date_string(date)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="emp_code not found")

    max_attempts = 15
    for attempt in range(max_attempts):
        # Read the record fresh
        current = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
        if current is None:
            raise HTTPException(status_code=404, detail="Record not found")

        # Truncate instants to whole seconds (R1)
        punch_in_dt = ms_to_dt(body.punch_in) if body.punch_in else None
        if punch_in_dt is not None:
            punch_in_dt = punch_in_dt.replace(microsecond=0)
        punch_out_dt = ms_to_dt(body.punch_out) if body.punch_out else None
        if punch_out_dt is not None:
            punch_out_dt = punch_out_dt.replace(microsecond=0)

        # Build new state from existing values and supplied fields
        new_status = body.status if body.status is not None else current.get("status")
        new_punch_in = punch_in_dt if body.punch_in is not None else current.get("punch_in")
        new_punch_out = punch_out_dt if body.punch_out is not None else current.get("punch_out")

        # Validation rules
        if new_status in ("ABSENT", "LEAVE"):
            if body.punch_in is not None or body.punch_out is not None:
                raise HTTPException(status_code=422, detail="ABSENT/LEAVE cannot have punch times")
            # ABSENT/LEAVE clears punch times
            new_punch_in = None
            new_punch_out = None
        else:
            # Presence status requires a punch_in
            if new_status in ("PRESENT", "WFH", "ON_DUTY") and new_punch_in is None:
                raise HTTPException(status_code=422, detail="Presence status requires punch_in")
            # punch_in must belong to the record's date (R1)
            if new_punch_in is not None:
                # Compute attendance date from punch_in_dt and employee shift
                att_date = attendance_date(new_punch_in, emp["shift_start"], emp["shift_end"])
                if att_date != date:
                    raise HTTPException(status_code=422, detail="punch_in does not belong to the record's date")
            # punch_out must be after punch_in and within 24 hours
            if new_punch_in is not None and new_punch_out is not None:
                if new_punch_out <= new_punch_in:
                    raise HTTPException(status_code=422, detail="punch_out must be after punch_in")
                if new_punch_out > new_punch_in + timedelta(hours=24):
                    raise HTTPException(status_code=422, detail="punch_out more than 24 hours after punch_in")

        # Compute derived fields
        late_minutes, work_hours, overtime_minutes, half_day = derive_fields(emp, new_status, new_punch_in, new_punch_out, date)

        # Prepare update document
        update = {}
        if body.status is not None:
            update["status"] = new_status
        update["punch_in"] = new_punch_in
        update["punch_out"] = new_punch_out
        # Derived fields are always updated (they may have changed due to punch time changes)
        update["work_hours"] = work_hours
        update["late_minutes"] = late_minutes
        update["overtime_minutes"] = overtime_minutes
        update["half_day"] = half_day

        # Build changes history
        changes = {}
        # status
        if body.status is not None and body.status != current.get("status"):
            changes["status"] = {"from": current.get("status"), "to": new_status}
        # punch_in
        from_val = current.get("punch_in")
        to_val = new_punch_in
        if from_val != to_val:
            changes["punch_in"] = {"from": dt_to_ms(from_val) if from_val is not None else None,
                                   "to": dt_to_ms(to_val) if to_val is not None else None}
        # punch_out
        from_val = current.get("punch_out")
        to_val = new_punch_out
        if from_val != to_val:
            changes["punch_out"] = {"from": dt_to_ms(from_val) if from_val is not None else None,
                                    "to": dt_to_ms(to_val) if to_val is not None else None}
        # work_hours
        if "work_hours" in update:
            from_val = current.get("work_hours")
            to_val = work_hours
            if from_val != to_val:
                changes["work_hours"] = {"from": from_val, "to": to_val}
        # late_minutes
        if "late_minutes" in update:
            from_val = current.get("late_minutes", 0)
            to_val = late_minutes
            if from_val != to_val:
                changes["late_minutes"] = {"from": from_val, "to": to_val}
        # overtime_minutes
        if "overtime_minutes" in update:
            from_val = current.get("overtime_minutes", 0)
            to_val = overtime_minutes
            if from_val != to_val:
                changes["overtime_minutes"] = {"from": from_val, "to": to_val}
        # half_day
        if "half_day" in update:
            from_val = current.get("half_day", False)
            to_val = half_day
            if from_val != to_val:
                changes["half_day"] = {"from": from_val, "to": to_val}

        if not changes:
            raise HTTPException(status_code=422, detail="No changes to apply")

        # Prepare history entry
        history_entry = {
            "at": dt_to_ms(datetime.now(tz=timezone.utc)),
            "by": body.regularized_by,
            "reason": body.reason,
            "changes": changes
        }

        # Build match filter: guard on _id and current state of the fields we are about to update (except history)
        match_filter = {
            "_id": current["_id"],
            "status": current.get("status"),
            "punch_in": current.get("punch_in"),
            "punch_out": current.get("punch_out"),
        }
        # For history, we can match on length and existence
        if "history" in current:
            match_filter["history"] = {"$size": len(current["history"])}
        else:
            match_filter["history"] = {"$exists": False}

        update_with_history = {"$set": update, "$push": {"history": history_entry}}
        result = db.attendance_logs.update_one(match_filter, update_with_history)
        if result.modified_count == 1:
            # Success
            updated_doc = db.attendance_logs.find_one({"_id": current["_id"]})
            updated_doc.pop("_id", None)
            if updated_doc.get("punch_in") is not None:
                updated_doc["punch_in"] = dt_to_ms(updated_doc["punch_in"])
            if updated_doc.get("punch_out") is not None:
                updated_doc["punch_out"] = dt_to_ms(updated_doc["punch_out"])
            return updated_doc
        # else retry after a tiny random sleep
        time.sleep(random.uniform(0, 0.02))

    # If we reach here, all attempts failed
    raise HTTPException(status_code=409, detail="Could not update record due to conflict")


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    # Validate month format
    if not re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])', month):
        raise HTTPException(status_code=422, detail="Invalid month format. Expected YYYY-MM")
    # Check employee exists
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="emp_code not found")
    # Parse year and month
    year, month_num = map(int, month.split('-'))
    # First and last day of the month as strings YYYY-MM-DD
    first_day = f"{year:04d}-{month_num:02d}-01"
    last_day = f"{year:04d}-{month_num:02d}-{calendar.monthrange(year, month_num)[1]:02d}"
    # MongoDB aggregation
    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": first_day,
                    "$lte": last_day
                }
            }
        },
        {
            "$group": {
                "_id": None,
                "leave_days": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
                    }
                },
                "total_late_minutes": {"$sum": "$late_minutes"},
                "total_overtime_minutes": {"$sum": "$overtime_minutes"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 2]},
                                    {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 6]}
                                ]
                            },
                            {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                            0
                        ]
                    }
                }
            }
        }
    ]
    result = list(db.attendance_logs.aggregate(pipeline))
    if not result:
        # No records for the month
        agg = {}
    else:
        agg = result[0]
    # Compute working days using helper
    working_days = compute_working_days(emp["joined_on"], year, month_num)
    present_days = agg.get("present_days", 0)
    # Ensure present_days is float (could be int if no half days)
    present_days = float(present_days)
    leave_days = agg.get("leave_days", 0)
    late_count = agg.get("late_count", 0)
    total_late_minutes = agg.get("total_late_minutes", 0)
    total_overtime_minutes = agg.get("total_overtime_minutes", 0)
    if working_days == 0:
        attendance_pct = None
    else:
        attendance_pct = (Decimal(str(present_days)) * 100 / Decimal(working_days)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        attendance_pct = float(attendance_pct)
    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct
    }


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #


@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    # Validate month format
    if not re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])', month):
        raise HTTPException(status_code=422, detail="Invalid month format. Expected YYYY-MM")
    # Parse year and month
    year, month_num = map(int, month.split('-'))
    # First and last day of the month as strings YYYY-MM-DD
    first_day = f"{year:04d}-{month_num:02d}-01"
    last_day = f"{year:04d}-{month_num:02d}-{calendar.monthrange(year, month_num)[1]:02d}"

    # Match employees: joined_on <= last_day and optional department filter
    emp_match = {"joined_on": {"$lte": last_day}}
    if department:
        emp_match["department"] = department

    # Lookup attendance logs for the month
    lookup_pipeline = [
        {
            "$match": {
                "$expr": {
                    "$and": [
                        {"$eq": ["$emp_code", "$$emp_code"]},
                        {"$gte": ["$date", first_day]},
                        {"$lte": ["$date", last_day]}
                    ]
                }
            }
        }
    ]

    # Aggregation pipeline starting from employees
    pipeline = [
        {"$match": emp_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"emp_code": "$emp_code"},
                "pipeline": lookup_pipeline,
                "as": "logs"
            }
        },
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {
            "$group": {
                "_id": {
                    "department": "$department",
                    "emp_code": "$emp_code"
                },
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}}, 2]},
                                    {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}}, 6]}
                                ]
                            },
                            {"$cond": [{"$eq": ["$logs.half_day", True]}, 0.5, 1]},
                            0
                        ]
                    }
                },
                "total_work_hours": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$ne": ["$logs.work_hours", None]}
                                ]
                            },
                            "$logs.work_hours",
                            0
                        ]
                    }
                },
                "count_work_hours": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$ne": ["$logs.work_hours", None]}
                                ]
                            },
                            1,
                            0
                        ]
                    }
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$logs.late_minutes", 0]}, 1, 0]}
                },
                "total_late_minutes": {"$sum": "$logs.late_minutes"},
                "leave_count": {
                    "$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}
                },
                "on_duty_count": {
                    "$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}
                }
            }
        },
        {
            "$group": {
                "_id": "$_id.department",
                "headcount": {"$sum": 1},
                "present_days": {"$sum": "$present_days"},
                "total_work_hours": {"$sum": "$total_work_hours"},
                "count_work_hours": {"$sum": "$count_work_hours"},
                "late_count": {"$sum": "$late_count"},
                "total_late_minutes": {"$sum": "$total_late_minutes"},
                "leave_count": {"$sum": "$leave_count"},
                "on_duty_count": {"$sum": "$on_duty_count"}
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "present_days": 1,
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": ["$count_work_hours", 0]},
                        {"$divide": ["$total_work_hours", "$count_work_hours"]},
                        None
                    ]
                },
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1
            }
        },
        {"$sort": {"department": 1}}
    ]

    # Execute aggregation
    results = list(db.employees.aggregate(pipeline))

    # Post-process: round avg_work_hours to 2 decimals half-up using Decimal
    for res in results:
        if res["avg_work_hours"] is not None:
            # Convert to string to avoid floating point issues, then quantize
            res["avg_work_hours"] = float(
                Decimal(str(res["avg_work_hours"])).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            )

    return results