
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError


load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "employee_attendance")

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

client = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=3000,
)
db = client[MONGO_DB]
employees = db["employees"]
attendance_logs = db["attendance_logs"]

PRESENT_STATUSES = {"PRESENT", "WFH", "ON_DUTY"}
ALL_STATUSES = PRESENT_STATUSES | {"ABSENT", "LEAVE"}

app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
)



# Startup and database indexes


@app.on_event("startup")
def create_indexes():
    
    employees.create_index(
        [("emp_code", ASCENDING)],
        unique=True,
        name="uq_employees_emp_code",
    )
    employees.create_index(
        [("department", ASCENDING), ("emp_code", ASCENDING)],
        name="ix_employees_department_emp_code",
    )
    employees.create_index(
        [("joined_on", ASCENDING), ("department", ASCENDING)],
        name="ix_employees_joined_department",
    )

    attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique=True,
        name="uq_attendance_emp_date",
    )
    attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING)],
        name="ix_attendance_date_emp",
    )
    attendance_logs.create_index(
        [("date", ASCENDING), ("status", ASCENDING)],
        name="ix_attendance_date_status",
    )
    attendance_logs.create_index(
        [("emp_code", ASCENDING), ("punch_in", DESCENDING)],
        name="ix_attendance_emp_punch_in",
    )


# Common helpers

def public_document(doc: dict) -> dict:
    """Remove MongoDB's internal identifier from API responses."""
    doc.pop("_id", None)
    return doc


def to_utc_datetime(epoch_ms: int) -> datetime:
    """Convert epoch milliseconds to a whole-second UTC datetime."""
    seconds = epoch_ms // 1000
    return datetime.fromtimestamp(seconds, tz=UTC)


def to_epoch_ms(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp()) * 1000


def round_half_up(value: float, places: int = 2) -> float:
    quantum = Decimal("1").scaleb(-places)
    result = Decimal(str(value)).quantize(
        quantum,
        rounding=ROUND_HALF_UP,
    )
    return float(result)


def validate_date_string(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("Date must use YYYY-MM-DD format")
    if parsed.isoformat() != value:
        raise ValueError("Date must use YYYY-MM-DD format")
    return value


def validate_time_string(value: str) -> str:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("Time must use HH:MM format")
    return value


def page_result(
    collection,
    query: dict,
    page: int,
    page_size: int,
    sort: list,
) -> dict:
    total = collection.count_documents(query)
    cursor = (
        collection.find(query, {"_id": 0})
        .sort(sort)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [public_document(doc) for doc in cursor],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# Request models

class EmployeeCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("name", "department")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Must not be blank")
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip()

    @field_validator("shift_start", "shift_end")
    @classmethod
    def validate_shift_time(cls, value: str) -> str:
        return validate_time_string(value)

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value: str) -> str:
        return validate_date_string(value)

    @field_validator("shift_end")
    @classmethod
    def shift_times_must_differ(
        cls,
        value: str,
        info,
    ) -> str:
        start = info.data.get("shift_start")
        if start is not None and start == value:
            raise ValueError("shift_start and shift_end must differ")
        return value


# Health endpoint

@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="MongoDB is unavailable",
        )


# Employee endpoints

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeCreate):
    now = datetime.now(UTC).replace(microsecond=0)

    document = body.model_dump()
    document["created_at"] = now

    try:
        employees.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        )
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="Database operation failed",
        )

    
    
    document.pop("_id", None)
    document["created_at"] = to_epoch_ms(document["created_at"])
    return document



@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = {}
    if department is not None:
        query["department"] = department

    try:
        return page_result(
            employees,
            query,
            page,
            page_size,
            [("emp_code", ASCENDING)],
        )
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="Database operation failed",
        )

# Attendance request models

from pydantic import field_validator


class PunchInRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(
        default=None, strict=True, ge=100000000000,
        le=4102444800000
    )
    status: str = "PRESENT"

    @field_validator("status")
    @classmethod
    def validate_punch_in_status(cls, value):
        if value not in PRESENT_STATUSES:
            raise ValueError("Invalid punch-in status")
        return value


class PunchOutRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(
        default=None, strict=True, ge=100000000000,
        le=4102444800000
    )


class RegularizeRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)
    status: Optional[str] = None
    punch_in: Optional[int] = Field(
        default=None, strict=True, ge=100000000000,
        le=4102444800000
    )
    punch_out: Optional[int] = Field(
        default=None, strict=True, ge=100000000000,
        le=4102444800000
    )

    @field_validator("status")
    @classmethod
    def validate_regularization_status(cls, value):
        if value is not None and value not in ALL_STATUSES:
            raise ValueError("Invalid attendance status")
        return value

    @field_validator("reason", "regularized_by")
    @classmethod
    def strip_audit_text(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Value must not be blank")
        return value


# Attendance calculation and serialization helpers

def normalized_datetime(value: datetime) -> datetime:
    """MongoDB returns naive UTC datetimes by default."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def instant_to_ist(value: datetime) -> datetime:
    return normalized_datetime(value).astimezone(IST)


def attendance_date_for(punch_in: datetime, employee: dict) -> str:
    local = instant_to_ist(punch_in)
    start = employee["shift_start"]
    end = employee["shift_end"]

    # Overnight shift: early-morning punch-in belongs to yesterday's shift.
    if end <= start and local.strftime("%H:%M") < end:
        local = local - timedelta(days=1)

    return local.date().isoformat()


def calculate_late_minutes(punch_in: datetime, employee: dict) -> int:
    local = instant_to_ist(punch_in)
    shift_hour, shift_minute = map(
        int, employee["shift_start"].split(":")
    )
    shift_start = local.replace(
        hour=shift_hour,
        minute=shift_minute,
        second=0,
        microsecond=0,
    )

    # For overnight shifts, the start belongs to the attendance date.
    attendance_day = date.fromisoformat(
        attendance_date_for(punch_in, employee)
    )
    shift_start = datetime.combine(
        attendance_day,
        time(shift_hour, shift_minute),
        tzinfo=IST,
    )

    elapsed = int((local - shift_start).total_seconds() // 60)
    elapsed_seconds = (local - shift_start).total_seconds()

    return elapsed if elapsed_seconds > 600 else 0


def calculate_work_hours(
    punch_in: datetime,
    punch_out: datetime,
) -> float:
    seconds = (
        normalized_datetime(punch_out)
        - normalized_datetime(punch_in)
    ).total_seconds()

    return round_half_up(seconds / 3600, 2)


def calculate_overtime(
    punch_out: datetime,
    employee: dict,
    attendance_day: str,
) -> int:
    end_hour, end_minute = map(
        int, employee["shift_end"].split(":")
    )
    day = date.fromisoformat(attendance_day)

    if employee["shift_end"] <= employee["shift_start"]:
        day += timedelta(days=1)

    shift_end = datetime.combine(
        day,
        time(end_hour, end_minute),
        tzinfo=IST,
    )
    out_local = instant_to_ist(punch_out)
    seconds = (out_local - shift_end).total_seconds()

    minutes = int(seconds // 60)
    return minutes if seconds >= 1800 else 0


def serialize_attendance(doc: dict) -> dict:
    """Convert all API-visible instants to epoch milliseconds."""
    doc = dict(doc)
    doc.pop("_id", None)

    for key in ("punch_in", "punch_out"):
        doc[key] = to_epoch_ms(doc.get(key))

    doc.setdefault("work_hours", None)
    doc.setdefault("late_minutes", 0)
    doc.setdefault("overtime_minutes", 0)
    doc.setdefault("half_day", False)
    doc.setdefault("history", [])

    history = []
    for entry in doc["history"]:
        item = dict(entry)
        item["at"] = to_epoch_ms(item.get("at"))

        changes = {}
        for field, change in item.get("changes", {}).items():
            change = dict(change)
            if field in ("punch_in", "punch_out"):
                change["from"] = to_epoch_ms(change.get("from"))
                change["to"] = to_epoch_ms(change.get("to"))
            changes[field] = change

        item["changes"] = changes
        history.append(item)

    doc["history"] = history
    return doc


# POST /attendance/punch-in


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInRequest):
    employee = employees.find_one({"emp_code": body.emp_code})
    if employee is None:
        raise HTTPException(404, "Employee not found")

    punched_at = (
        to_utc_datetime(body.punched_at)
        if body.punched_at is not None
        else datetime.now(UTC).replace(microsecond=0)
    )

    attendance_day = attendance_date_for(punched_at, employee)

    document = {
        "emp_code": body.emp_code,
        "date": attendance_day,
        "status": body.status,
        "punch_in": punched_at,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": calculate_late_minutes(
            punched_at, employee
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        attendance_logs.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(
            409, "Already punched in for this attendance date"
        )
    except PyMongoError:
        raise HTTPException(503, "Database operation failed")

    return serialize_attendance(document)



# POST /attendance/punch-out


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutRequest):
    employee = employees.find_one({"emp_code": body.emp_code})
    if employee is None:
        raise HTTPException(404, "Employee not found")

    punched_at = (
        to_utc_datetime(body.punched_at)
        if body.punched_at is not None
        else datetime.now(UTC).replace(microsecond=0)
    )

    # Find the latest punch-in that occurred before this punch-out.
    record = attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": punched_at},
            "status": {"$in": list(PRESENT_STATUSES)},
        },
        sort=[("punch_in", DESCENDING)],
    )

    if record is None:
        raise HTTPException(404, "No matching punch-in found")

    punch_in_time = normalized_datetime(record["punch_in"])
    punched_at = normalized_datetime(punched_at)

    if punched_at <= punch_in_time:
        raise HTTPException(
            422, "Punch-out must be after punch-in"
        )

    if punched_at > punch_in_time + timedelta(hours=24):
        raise HTTPException(
            422, "Punch-out cannot be more than 24 hours later"
        )

    if record.get("punch_out") is not None:
        raise HTTPException(409, "Already punched out")

    work_hours = calculate_work_hours(
        punch_in_time, punched_at
    )
    overtime = calculate_overtime(
        punched_at, employee, record["date"]
    )

    update = attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": punched_at,
                "work_hours": work_hours,
                "overtime_minutes": overtime,
                "half_day": work_hours < 4.50,
            }
        },
    )

    if update.modified_count != 1:
        raise HTTPException(
            409, "Attendance record was already updated"
        )

    updated = attendance_logs.find_one(
        {"_id": record["_id"]}
    )
    return serialize_attendance(updated)



# GET /attendance


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = {}

    if emp_code is not None:
        query["emp_code"] = emp_code

    if date_from is not None:
        validate_date_string(date_from)
    if date_to is not None:
        validate_date_string(date_to)

    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must not exceed date_to")

    if date_from or date_to:
        query["date"] = {}
        if date_from:
            query["date"]["$gte"] = date_from
        if date_to:
            query["date"]["$lte"] = date_to

    if status is not None:
        if status not in ALL_STATUSES:
            raise HTTPException(422, "Invalid attendance status")
        query["status"] = status

    try:
        total = attendance_logs.count_documents(query)
        cursor = (
            attendance_logs.find(query, {"_id": 0})
            .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
            .skip((page - 1) * page_size)
            .limit(page_size)
        )

        return {
            "items": [
                serialize_attendance(doc) for doc in cursor
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    except PyMongoError:
        raise HTTPException(503, "Database operation failed")



# PATCH /attendance/{emp_code}/{date}


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeRequest,
):
    try:
        validate_date_string(date)
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    employee = employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(404, "Employee not found")

    record = attendance_logs.find_one(
        {"emp_code": emp_code, "date": date}
    )
    if record is None:
        raise HTTPException(404, "Attendance record not found")

    old = {
        "status": record["status"],
        "punch_in": record.get("punch_in"),
        "punch_out": record.get("punch_out"),
        "work_hours": record.get("work_hours"),
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day": record.get("half_day", False),
    }

    new = dict(old)
    if body.status is not None:
        new["status"] = body.status
    if body.punch_in is not None:
        new["punch_in"] = to_utc_datetime(body.punch_in)
    if body.punch_out is not None:
        new["punch_out"] = to_utc_datetime(body.punch_out)

    if new["status"] in {"ABSENT", "LEAVE"}:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(
                422, "ABSENT and LEAVE cannot have punch times"
            )
        new["punch_in"] = None
        new["punch_out"] = None
        new["work_hours"] = None
        new["late_minutes"] = 0
        new["overtime_minutes"] = 0
        new["half_day"] = False
    else:
        if new["punch_in"] is None:
            raise HTTPException(
                422, "Presence status requires punch_in"
            )

        if attendance_date_for(new["punch_in"], employee) != date:
            raise HTTPException(
                422, "punch_in must belong to the record attendance date"
            )

        new["late_minutes"] = calculate_late_minutes(
            new["punch_in"], employee
        )

        if new["punch_out"] is None:
            new["work_hours"] = None
            new["overtime_minutes"] = 0
            new["half_day"] = False
        else:
            if normalized_datetime(new["punch_out"]) <= normalized_datetime(new["punch_in"]):
                raise HTTPException(
                    422, "punch_out must be after punch_in"
                )
            if normalized_datetime(new["punch_out"]) > normalized_datetime(new["punch_in"]) + timedelta(hours=24):
                raise HTTPException(
                    422, "Punch-out cannot be more than 24 hours later"
                )

            new["work_hours"] = calculate_work_hours(
                new["punch_in"], new["punch_out"]
            )
            new["overtime_minutes"] = calculate_overtime(
                new["punch_out"], employee, date
            )
            new["half_day"] = new["work_hours"] < 4.50

    changes = {}
    fields = (
        "status", "punch_in", "punch_out", "work_hours",
        "late_minutes", "overtime_minutes", "half_day",
    )

    for field in fields:
        before = old[field]
        after = new[field]
        if isinstance(before, datetime) and isinstance(after, datetime):
            different = normalized_datetime(before) != normalized_datetime(after)
        else:
            different = before != after

        if different:
            changes[field] = {"from": before, "to": after}

    if not changes:
        raise HTTPException(422, "Regularization changes no fields")

    history_entry = {
        "at": datetime.now(UTC).replace(microsecond=0),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }


    # Compare-and-set prevents concurrent updates from overwriting history.
    expected = {
        "_id": record["_id"],
        "history": record.get("history", []),
        "status": record["status"],
        "work_hours": record.get("work_hours"),
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day": record.get("half_day", False),
    }

    for field in ("punch_in", "punch_out"):
        if field in record:
            expected[field] = record[field]
        else:
            expected[field] = {"$exists": False}

    update_fields = {
        key: new[key]
        for key in fields
        if key in changes
    }

    result = attendance_logs.update_one(
        expected,
        {
            "$set": update_fields,
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            409, "Record changed concurrently; reload and retry"
        )

    updated = attendance_logs.find_one(
        {"_id": record["_id"]}
    )
    return serialize_attendance(updated)

# -------------------------------------------------------------------
# Analytics helpers
# -------------------------------------------------------------------

def month_bounds(month: str):
    """Return the first day and exclusive end of a YYYY-MM month."""
    try:
        first_day = date.fromisoformat(f"{month}-01")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format",
        )

    if first_day.strftime("%Y-%m") != month:
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format",
        )

    if first_day.month == 12:
        next_month = date(first_day.year + 1, 1, 1)
    else:
        next_month = date(first_day.year, first_day.month + 1, 1)

    return first_day.isoformat(), next_month.isoformat(), next_month - timedelta(days=1)


def count_working_days(start: date, end: date) -> int:
    """Count Monday-Friday inclusively, with no holiday calendar."""
    if start > end:
        return 0

    count = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


def build_employee_monthly_pipeline(emp_code, month_start, month_end):
    """Build the exact MongoDB pipeline used by the employee monthly endpoint."""
    return [{'$match': {'emp_code': emp_code, 'date': {'$gte': month_start, '$lt': month_end}}}, {'$set': {'_calendar_date': {'$dateFromString': {'dateString': '$date'}}}}, {'$set': {'_weekday': {'$dayOfWeek': '$_calendar_date'}}}, {'$group': {'_id': None, 'present_days': {'$sum': {'$cond': [{'$and': [{'$in': ['$status', list(PRESENT_STATUSES)]}, {'$gte': ['$_weekday', 2]}, {'$lte': ['$_weekday', 6]}]}, {'$cond': ['$half_day', 0.5, 1.0]}, 0]}}, 'leave_days': {'$sum': {'$cond': [{'$eq': ['$status', 'LEAVE']}, 1, 0]}}, 'late_count': {'$sum': {'$cond': [{'$gt': ['$late_minutes', 0]}, 1, 0]}}, 'total_late_minutes': {'$sum': '$late_minutes'}, 'total_overtime_minutes': {'$sum': '$overtime_minutes'}}}]


def build_department_summary_pipeline(employee_match, month_start, month_end):
    """Build the exact MongoDB pipeline used by the department summary endpoint."""
    return [{'$match': employee_match}, {'$lookup': {'from': 'attendance_logs', 'let': {'code': '$emp_code'}, 'pipeline': [{'$match': {'$expr': {'$and': [{'$eq': ['$emp_code', '$$code']}, {'$gte': ['$date', month_start]}, {'$lt': ['$date', month_end]}]}}}, {'$set': {'_calendar_date': {'$dateFromString': {'dateString': '$date'}}}}, {'$set': {'_weekday': {'$dayOfWeek': '$_calendar_date'}}}, {'$group': {'_id': None, 'present_days': {'$sum': {'$cond': [{'$and': [{'$in': ['$status', list(PRESENT_STATUSES)]}, {'$gte': ['$_weekday', 2]}, {'$lte': ['$_weekday', 6]}]}, {'$cond': ['$half_day', 0.5, 1.0]}, 0]}}, 'work_hours_values': {'$push': {'$cond': [{'$and': [{'$in': ['$status', list(PRESENT_STATUSES)]}, {'$ne': ['$work_hours', None]}]}, '$work_hours', None]}}, 'late_count': {'$sum': {'$cond': [{'$gt': ['$late_minutes', 0]}, 1, 0]}}, 'total_late_minutes': {'$sum': '$late_minutes'}, 'leave_count': {'$sum': {'$cond': [{'$eq': ['$status', 'LEAVE']}, 1, 0]}}, 'on_duty_count': {'$sum': {'$cond': [{'$eq': ['$status', 'ON_DUTY']}, 1, 0]}}}}], 'as': 'monthly_metrics'}}, {'$set': {'metrics': {'$ifNull': [{'$arrayElemAt': ['$monthly_metrics', 0]}, {'present_days': 0, 'work_hours_values': [], 'late_count': 0, 'total_late_minutes': 0, 'leave_count': 0, 'on_duty_count': 0}]}}}, {'$group': {'_id': '$department', 'headcount': {'$sum': 1}, 'present_days': {'$sum': '$metrics.present_days'}, 'work_hours_values': {'$push': '$metrics.work_hours_values'}, 'late_count': {'$sum': '$metrics.late_count'}, 'total_late_minutes': {'$sum': '$metrics.total_late_minutes'}, 'leave_count': {'$sum': '$metrics.leave_count'}, 'on_duty_count': {'$sum': '$metrics.on_duty_count'}}}, {'$set': {'all_work_hours': {'$reduce': {'input': '$work_hours_values', 'initialValue': [], 'in': {'$concatArrays': ['$$value', '$$this']}}}}}, {'$set': {'avg_work_hours_raw': {'$avg': '$all_work_hours'}}}, {'$project': {'_id': 0, 'department': '$_id', 'headcount': 1, 'present_days': 1, 'avg_work_hours': {'$cond': [{'$eq': ['$avg_work_hours_raw', None]}, None, {'$round': ['$avg_work_hours_raw', 2]}]}, 'late_count': 1, 'total_late_minutes': 1, 'leave_count': 1, 'on_duty_count': 1}}, {'$sort': {'department': 1}}]


def build_late_leaderboard_pipeline(month_start, month_end, eligible_codes=None):
    """Build the shared aggregation used to total each employee's late minutes."""
    match = {
        "date": {"$gte": month_start, "$lt": month_end},
        "late_minutes": {"$gt": 0},
    }
    if eligible_codes is not None:
        match["emp_code"] = {"$in": eligible_codes}

    return [
        {"$match": match},
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
    ]

def build_department_trend_pipeline(department, end_text, start_text, end_exclusive, start_datetime, end_datetime_exclusive):
    """Build the exact MongoDB pipeline used by the department trend endpoint."""
    return [{'$match': {'department': department, 'joined_on': {'$lte': end_text}}}, {'$lookup': {'from': 'attendance_logs', 'let': {'employee_code': '$emp_code'}, 'pipeline': [{'$match': {'$expr': {'$and': [{'$eq': ['$emp_code', '$$employee_code']}, {'$gte': ['$date', start_text]}, {'$lt': ['$date', end_exclusive]}]}}}, {'$project': {'_id': 0, 'date': 1, 'status': 1, 'half_day': 1, 'late_minutes': 1}}], 'as': 'logs'}}, {'$unwind': {'path': '$logs', 'preserveNullAndEmptyArrays': True}}, {'$set': {'_date': {'$cond': [{'$ifNull': ['$logs.date', False]}, {'$dateFromString': {'dateString': '$logs.date'}}, start_datetime]}}}, {'$group': {'_id': {'emp_code': '$emp_code', 'date': '$_date'}, 'joined_on': {'$first': '$joined_on'}, 'status': {'$first': '$logs.status'}, 'half_day': {'$first': '$logs.half_day'}, 'late_minutes': {'$first': '$logs.late_minutes'}}}, {'$project': {'_id': 0, 'emp_code': '$_id.emp_code', '_date': '$_id.date', 'joined_on': 1, 'status': 1, 'half_day': 1, 'late_minutes': 1}}, {'$densify': {'field': '_date', 'partitionByFields': ['emp_code'], 'range': {'step': 1, 'unit': 'day', 'bounds': [start_datetime, end_datetime_exclusive]}}}, {'$group': {'_id': '$_date', 'headcount': {'$sum': {'$cond': [{'$lte': ['$joined_on', {'$dateToString': {'date': '$_date', 'format': '%Y-%m-%d', 'timezone': 'UTC'}}]}, 1, 0]}}, 'present_count': {'$sum': {'$cond': [{'$in': ['$status', list(PRESENT_STATUSES)]}, {'$cond': [{'$eq': ['$half_day', True]}, 0.5, 1.0]}, 0]}}, 'late_count': {'$sum': {'$cond': [{'$gt': ['$late_minutes', 0]}, 1, 0]}}}}, {'$set': {'date': {'$dateToString': {'date': '$_id', 'format': '%Y-%m-%d', 'timezone': 'UTC'}}, 'is_working_day': {'$and': [{'$gte': [{'$dayOfWeek': '$_id'}, 2]}, {'$lte': [{'$dayOfWeek': '$_id'}, 6]}]}}}, {'$set': {'attendance_rate': {'$cond': [{'$and': ['$is_working_day', {'$gt': ['$headcount', 0]}]}, {'$round': [{'$multiply': [{'$divide': ['$present_count', '$headcount']}, 100]}, 4]}, None]}}}, {'$setWindowFields': {'sortBy': {'_id': 1}, 'output': {'moving_avg_7d': {'$avg': '$attendance_rate', 'window': {'documents': [-6, 0]}}}}}, {'$project': {'_id': 0, 'date': 1, 'is_working_day': 1, 'headcount': 1, 'present_count': 1, 'late_count': 1, 'attendance_rate': 1, 'moving_avg_7d': {'$cond': [{'$eq': ['$moving_avg_7d', None]}, None, {'$round': ['$moving_avg_7d', 4]}]}}}, {'$sort': {'date': 1}}]

# GET /analytics/employees/{emp_code}/monthly

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
):
    employee = employees.find_one(
        {"emp_code": emp_code},
        {"_id": 0, "emp_code": 1, "joined_on": 1},
    )
    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    month_start, month_end, last_day = month_bounds(month)

    # An employee's working days begin on their join date.
    joined_on = date.fromisoformat(employee["joined_on"])
    effective_start = max(
        joined_on,
        date.fromisoformat(month_start),
    )
    working_days = count_working_days(
        effective_start,
        last_day,
    )

    # Calculate monthly metrics from stored derived values.
    pipeline = build_employee_monthly_pipeline(emp_code, month_start, month_end)

    try:
        results = list(attendance_logs.aggregate(pipeline))
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="Database operation failed",
        )

    metrics = results[0] if results else {}

    present_days = round_half_up(
        metrics.get("present_days", 0),
        2,
    )

    attendance_pct = (
        round_half_up(
            present_days / working_days * 100,
            2,
        )
        if working_days > 0
        else None
    )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": metrics.get("leave_days", 0),
        "late_count": metrics.get("late_count", 0),
        "total_late_minutes": metrics.get("total_late_minutes", 0),
        "total_overtime_minutes": metrics.get(
            "total_overtime_minutes", 0
        ),
        "attendance_pct": attendance_pct,
    }


# GET /analytics/departments/summary

@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    department: Optional[str] = None,
):
    month_start, month_end, last_day = month_bounds(month)

    employee_match = {
        "joined_on": {"$lte": last_day.isoformat()}
    }
    if department is not None:
        employee_match["department"] = department

    # Start from employees so that people with zero attendance logs
    # still contribute to the department headcount.
    pipeline = build_department_summary_pipeline(employee_match, month_start, month_end)

    try:
        items = list(employees.aggregate(pipeline))
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="Database operation failed",
        )

    # Use explicit half-up rounding for reported numeric values.
    for item in items:
        item["present_days"] = round_half_up(
            item["present_days"], 2
        )
        if item["avg_work_hours"] is not None:
            item["avg_work_hours"] = round_half_up(
                item["avg_work_hours"], 2
            )

    return {"month": month, "items": items}


# GET /analytics/leaderboard/late


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    department: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=50),
):
    month_start, month_end, _ = month_bounds(month)

    # Only include employees who exist and have joined by month end.
    employee_filter = {}
    if department is not None:
        employee_filter["department"] = department

    try:
        employee_docs = list(
            employees.find(
                employee_filter,
                {"_id": 0, "emp_code": 1, "name": 1, "department": 1},
            )
        )
        employee_map = {
            item["emp_code"]: item for item in employee_docs
        }

        pipeline = build_late_leaderboard_pipeline(
            month_start, month_end, list(employee_map) if department is not None else None
        )

        totals = list(attendance_logs.aggregate(pipeline))
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="Database operation failed",
        )

    # Exclude orphan logs and employees who joined after the month ended.
    eligible = []
    for item in totals:
        employee = employee_map.get(item["_id"])
        if employee is None:
            continue

        joined_on = date.fromisoformat(
            employees.find_one(
                {"emp_code": item["_id"]},
                {"_id": 0, "joined_on": 1},
            )["joined_on"]
        )
        if joined_on > date.fromisoformat(month_end) - timedelta(days=1):
            continue

        eligible.append(
            {
                "emp_code": item["_id"],
                "name": employee.get("name"),
                "department": employee.get("department"),
                "total_late_minutes": item["total_late_minutes"],
                "late_count": item["late_count"],
            }
        )

    # Standard competition ranking: 1, 2, 2, 4.
    ranked = []
    previous_total = None
    current_rank = 0

    for index, item in enumerate(eligible, start=1):
        total = item["total_late_minutes"]
        if total != previous_total:
            current_rank = index
            previous_total = total

        ranked.append({"rank": current_rank, **item})

    # Apply limit after ranking so ties at the cutoff are retained.
    if len(ranked) > limit:
        cutoff_total = ranked[limit - 1]["total_late_minutes"]
        ranked = [
            item for item in ranked
            if item["rank"] <= ranked[limit - 1]["rank"]
            or item["total_late_minutes"] == cutoff_total
        ]

    return {
        "month": month,
        "department": department,
        "items": ranked,
    }

# GET /analytics/departments/{department}/trend

@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_: str = Query(..., alias="from"),
    to: str = Query(..., alias="to"),
):
    # Validate ISO calendar dates.
    try:
        start_day = date.fromisoformat(from_)
        end_day = date.fromisoformat(to)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="from and to must use YYYY-MM-DD format",
        )

    if start_day.isoformat() != from_ or end_day.isoformat() != to:
        raise HTTPException(
            status_code=422,
            detail="from and to must use YYYY-MM-DD format",
        )

    if end_day < start_day:
        raise HTTPException(
            status_code=422,
            detail="to must be on or after from",
        )

    if (end_day - start_day).days + 1 > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days",
        )

    # A department exists if it has any employee, regardless of joining date.
    if employees.find_one({"department": department}) is None:
        raise HTTPException(404, "Department not found")

    start_text = start_day.isoformat()
    end_text = end_day.isoformat()
    end_exclusive = (end_day + timedelta(days=1)).isoformat()

    start_datetime = datetime.combine(
        start_day, time.min, tzinfo=UTC
    )
    end_datetime_exclusive = datetime.combine(
        end_day + timedelta(days=1), time.min, tzinfo=UTC
    )

    # Start with employees who have joined by the end of the requested
    # period. Seed each employee at the start date, then fill each date
    # per employee with MongoDB $densify.
    pipeline = build_department_trend_pipeline(department, end_text, start_text, end_exclusive, start_datetime, end_datetime_exclusive)

    try:
        items = list(employees.aggregate(pipeline))
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="Database operation failed",
        )

    return {"department": department, "items": items}

# GET /admin/explain/{endpoint}

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """Return executionStats for the same query/pipeline used by a real endpoint."""
    supported = {
        "attendance_list", "employee_monthly", "department_summary",
        "late_leaderboard", "department_trend",
    }
    if endpoint not in supported:
        raise HTTPException(422, f"endpoint must be one of {sorted(supported)}")

    collection = attendance_logs
    explain_command = None

    try:
        if endpoint == "attendance_list":
            query = {}
            if emp_code is not None:
                query["emp_code"] = emp_code
            if date_from is not None:
                validate_date_string(date_from)
            if date_to is not None:
                validate_date_string(date_to)
            if date_from and date_to and date_from > date_to:
                raise HTTPException(422, "date_from must not exceed date_to")
            if date_from or date_to:
                query["date"] = {}
                if date_from:
                    query["date"]["$gte"] = date_from
                if date_to:
                    query["date"]["$lte"] = date_to
            if status is not None:
                if status not in ALL_STATUSES:
                    raise HTTPException(422, "Invalid attendance status")
                query["status"] = status
            explain_command = {
                "find": attendance_logs.name,
                "filter": query,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": page_size,
            }

        elif endpoint == "employee_monthly":
            if not emp_code or not month:
                raise HTTPException(422, "emp_code and month are required")
            if employees.find_one({"emp_code": emp_code}, {"_id": 1}) is None:
                raise HTTPException(404, "Employee not found")
            month_start, month_end, _ = month_bounds(month)
            collection = attendance_logs
            explain_command = {
                "aggregate": attendance_logs.name,
                "pipeline": build_employee_monthly_pipeline(emp_code, month_start, month_end),
                "cursor": {},
            }

        elif endpoint == "department_summary":
            if not month:
                raise HTTPException(422, "month is required")
            month_start, month_end, last_day = month_bounds(month)
            employee_match = {"joined_on": {"$lte": last_day.isoformat()}}
            if department is not None:
                employee_match["department"] = department
            collection = employees
            explain_command = {
                "aggregate": employees.name,
                "pipeline": build_department_summary_pipeline(employee_match, month_start, month_end),
                "cursor": {},
            }

        elif endpoint == "late_leaderboard":
            if not month:
                raise HTTPException(422, "month is required")
            month_start, month_end, _ = month_bounds(month)
            collection = attendance_logs
            # This is the same aggregation pipeline used by late_leaderboard.
            # Ranking/tie expansion happens in the endpoint after this aggregation.
            eligible_codes = None
            if department is not None:
                eligible_codes = employees.distinct("emp_code", {"department": department})
            explain_command = {
                "aggregate": attendance_logs.name,
                "pipeline": build_late_leaderboard_pipeline(
                    month_start, month_end, eligible_codes
                ),
                "cursor": {},
            }

        elif endpoint == "department_trend":
            if not department or not from_ or not to:
                raise HTTPException(422, "department, from and to are required")
            try:
                start_day = date.fromisoformat(from_)
                end_day = date.fromisoformat(to)
            except ValueError:
                raise HTTPException(422, "from and to must use YYYY-MM-DD format")
            if start_day.isoformat() != from_ or end_day.isoformat() != to:
                raise HTTPException(422, "from and to must use YYYY-MM-DD format")
            if end_day < start_day:
                raise HTTPException(422, "to must be on or after from")
            if (end_day - start_day).days + 1 > 92:
                raise HTTPException(422, "Date range cannot exceed 92 days")
            if employees.find_one({"department": department}, {"_id": 1}) is None:
                raise HTTPException(404, "Department not found")
            start_text = start_day.isoformat()
            end_text = end_day.isoformat()
            end_exclusive = (end_day + timedelta(days=1)).isoformat()
            start_datetime = datetime.combine(start_day, time.min, tzinfo=UTC)
            end_datetime_exclusive = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=UTC)
            collection = employees
            explain_command = {
                "aggregate": employees.name,
                "pipeline": build_department_trend_pipeline(
                    department, end_text, start_text, end_exclusive,
                    start_datetime, end_datetime_exclusive,
                ),
                "cursor": {},
            }

        raw_explain = collection.database.command({
            "explain": explain_command,
            "verbosity": "executionStats",
        })
        return {
            "endpoint": endpoint,
            "collection": collection.name,
            "explain": raw_explain,
        }
    except HTTPException:
        raise
    except PyMongoError:
        raise HTTPException(503, "Database explain operation failed")
