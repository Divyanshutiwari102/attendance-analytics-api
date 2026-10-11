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


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str = Field(..., pattern='^EMP\d{4,6}$')
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=120, pattern='^[^@\s]+@[^@\s]+\.[^@\s]+$')
    department: str = Field(..., min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern='^([01]\d|2[0-3]):[0-5]\d$')
    shift_end: str = Field(default="18:30", pattern='^([01]\d|2[0-3]):[0-5]\d$')
    joined_on: str
    @field_validator('joined_on')
    @classmethod
    def validate_joined_on(cls, v):
        return validate_iso_date_string(v)


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = Field(None, strict=True, ge=100000000000, le=4102444800000)
    status: str = Field(default="PRESENT", pattern='^(PRESENT|WFH|ON_DUTY)$')


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


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
