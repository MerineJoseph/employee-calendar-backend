# app/main.py
import os
import json
import httpx
import re
import datetime as dt
from pathlib import Path
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from app.db import engine, SessionLocal, Base
from app.models import CalendarEntry

app = FastAPI()

# ---------- Persistence (legacy JSON for one-time import) ----------
DATA_FILE = Path(__file__).parent / "calendar_data.json"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
VALID_STATIONS = {"StationA", "StationB"}

def normalize_station(station: str) -> str:
    return station if station in VALID_STATIONS else "StationA"

# ---------- CORS ----------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://employee-calendar-frontend.vercel.app",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def on_startup():
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # One-time import of legacy JSON into DB (if table empty and file exists)
    if DATA_FILE.exists():
        async with SessionLocal() as session:
            count = (await session.execute(select(CalendarEntry))).scalars().first()
            if not count:
                try:
                    with open(DATA_FILE, "r") as f:
                        data = json.load(f)

                    # Back-compat with old shapes (flat or stations)
                    stations_obj = data.get("stations")
                    if not stations_obj:
                        stations_obj = {
                            "StationA": {
                                "calendar_data": data.get("calendar_data", {}),
                                "calendar_times": data.get("calendar_times", {}),
                                "public_holidays": data.get("public_holidays", {}),
                            }
                        }

                    for station, buckets in stations_obj.items():
                        cal = buckets.get("calendar_data", {})
                        times = buckets.get("calendar_times", {})
                        ph   = buckets.get("public_holidays", {})
                        for d, status in cal.items():
                            if not DATE_RE.match(d):
                                continue
                            is_ph = d in ph
                            entry = CalendarEntry(
                                station=normalize_station(station),
                                date=dt.date.fromisoformat(d),
                                status=status,
                                time_label=times.get(d) or ("RD" if status == "Holiday" else "0 hours"),
                                is_public_holiday=is_ph,
                                holiday_name=ph.get(d) if is_ph else None,
                            )
                            session.add(entry)
                    await session.commit()
                    # Optionally delete the JSON file after import:
                    # DATA_FILE.unlink(missing_ok=True)
                except Exception:
                    # If import fails, just continue; DB is empty
                    pass

@app.get("/")
def root():
    return {"message": "Backend running"}

# ---------- Helpers ----------
def _date_or_400(value: str) -> dt.date:
    if not DATE_RE.match(value):
        raise HTTPException(status_code=400, detail="Invalid date format (expected YYYY-MM-DD)")
    return dt.date.fromisoformat(value)

# ---------- Routes ----------
@app.get("/calendar")
async def get_calendar_data(station: str = Query("StationA")):
    station = normalize_station(station)
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(CalendarEntry).where(CalendarEntry.station == station)
        )).scalars().all()

    calendar_data = {}
    calendar_times = {}
    public_holidays = {}

    for r in rows:
        key = r.date.isoformat()
        calendar_data[key] = r.status
        if r.time_label:
            calendar_times[key] = r.time_label
        if r.is_public_holiday and r.holiday_name:
            public_holidays[key] = r.holiday_name

    return {
        "station": station,
        "calendar_data": calendar_data,
        "public_holidays": public_holidays,
        "calendar_times": calendar_times,
    }

# Dummy login
@app.post("/login")
async def login(request: Request):
    data = await request.json()
    email = data.get("email")
    password = data.get("password")
    if email == "franklin@gmail.com" and password == "secret":
        return {"success": True, "message": "Login successful"}
    else:
        return {"success": False, "message": "Invalid credentials"}

# Reset a station
@app.post("/admin/reset")
async def reset_station(station: str = Query("StationA"), secret: str = Query(...)):  # required
    expected = os.environ.get("RENDER_RESET_SECRET")
    if not expected or secret != expected:
        raise HTTPException(status_code=403, detail="Forbidden")

    station = normalize_station(station)
    async with SessionLocal() as session:
        await session.execute(
            delete(CalendarEntry).where(CalendarEntry.station == station)
        )
        await session.commit()
    return {"success": True, "message": f"[{station}] wiped successfully."}

# Add/Update a single date (Working/Holiday) + optional time
@app.post("/calendar")
async def add_calendar_entry(request: Request, station: str = Query("StationA")):
    station = normalize_station(station)
    body = await request.json()
    d = body.get("date")
    status = body.get("status")
    time = (body.get("time") or "").strip()

    if status not in ["Working", "Holiday"]:
        return {"success": False, "message": "Invalid status"}

    try:
        date_obj = _date_or_400(d)
    except HTTPException as e:
        return {"success": False, "message": e.detail}

    async with SessionLocal() as session:
        # find existing entry for that station+date
        existing = (await session.execute(
            select(CalendarEntry).where(
                CalendarEntry.station == station,
                CalendarEntry.date == date_obj
            )
        )).scalars().first()

        if not existing:
            existing = CalendarEntry(
                station=station,
                date=date_obj,
                status=status,
                time_label=None,
                is_public_holiday=False,
                holiday_name=None,
            )
            session.add(existing)

        existing.status = status
        if time:
            existing.time_label = time
        else:
            existing.time_label = "RD" if status == "Holiday" else "0 hours"

        await session.commit()
    return {"success": True, "message": f"[{station}] Added {status} for {d}"}

# Add a single public holiday
@app.post("/calendar/public")
async def add_public_holiday(request: Request, station: str = Query("StationA")):
    station = normalize_station(station)
    body = await request.json()
    d = (body.get("date") or "").strip()
    name = (body.get("name") or "").strip()

    if not d or not name:
        return {"success": False, "message": "Missing date or name"}

    try:
        date_obj = _date_or_400(d)
    except HTTPException as e:
        return {"success": False, "message": e.detail}

    async with SessionLocal() as session:
        existing = (await session.execute(
            select(CalendarEntry).where(
                CalendarEntry.station == station,
                CalendarEntry.date == date_obj
            )
        )).scalars().first()

        if not existing:
            existing = CalendarEntry(
                station=station,
                date=date_obj,
                status="Holiday",
                time_label="RD",
                is_public_holiday=True,
                holiday_name=name,
            )
            session.add(existing)
        else:
            existing.status = "Holiday"
            existing.time_label = "RD"
            existing.is_public_holiday = True
            existing.holiday_name = name

        await session.commit()
    return {"success": True, "message": f"[{station}] Added public holiday {name} on {d}"}

# Remove a date (work/holiday/time) from a station
@app.delete("/calendar/{date}")
async def remove_calendar_date(date: str, station: str = Query("StationA")):
    station = normalize_station(station)
    try:
        date_obj = _date_or_400(date)
    except HTTPException as e:
        return {"success": False, "message": e.detail}

    async with SessionLocal() as session:
        await session.execute(
            delete(CalendarEntry).where(
                CalendarEntry.station == station,
                CalendarEntry.date == date_obj
            )
        )
        await session.commit()
    return {"success": True, "message": f"[{station}] Deleted {date}"}

# Bulk-load AU (QLD) public holidays for a given year into a station
@app.post("/calendar/public/auto")
async def fetch_qld_public_holidays(station: str = Query("StationA"), year: int = Query(2025)):
    station = normalize_station(station)
    url = f"https://date.nager.at/api/v3/PublicHolidays/{year}/AU"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=20)
            resp.raise_for_status()
            data = resp.json()

        added = 0
        async with SessionLocal() as session:
            for item in data:
                counties = item.get("counties") or []
                # Keep QLD-specific holidays and national holidays (no counties)
                if ("QLD" in "".join(counties)) or not counties:
                    d = item["date"]  # YYYY-MM-DD
                    name = item["localName"]
                    date_obj = dt.date.fromisoformat(d)

                    existing = (await session.execute(
                        select(CalendarEntry).where(
                            CalendarEntry.station == station,
                            CalendarEntry.date == date_obj
                        )
                    )).scalars().first()

                    if not existing:
                        existing = CalendarEntry(
                            station=station,
                            date=date_obj,
                            status="Holiday",
                            time_label="RD",
                            is_public_holiday=True,
                            holiday_name=name,
                        )
                        session.add(existing)
                        added += 1
                    else:
                        # Update to holiday
                        existing.status = "Holiday"
                        existing.time_label = "RD"
                        existing.is_public_holiday = True
                        existing.holiday_name = name

            await session.commit()

        return {"success": True, "message": f"[{station}] {added} holidays loaded for {year}."}

    except Exception as e:
        return {"success": False, "error": str(e)}

# Remove a single public holiday date from a station
@app.delete("/calendar/public/{date}")
async def remove_public_holiday(date: str, station: str = Query("StationA")):
    station = normalize_station(station)
    try:
        date_obj = _date_or_400(date)
    except HTTPException as e:
        return {"success": False, "message": e.detail}

    async with SessionLocal() as session:
        # just set back to non-holiday (or delete entirely—keeping behavior consistent with your previous API)
        await session.execute(
            delete(CalendarEntry).where(
                CalendarEntry.station == station,
                CalendarEntry.date == date_obj
            )
        )
        await session.commit()

    return {"success": True, "message": f"[{station}] Removed public holiday for {date}"}
