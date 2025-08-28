import os
import json
import httpx
from pathlib import Path
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# ---------- Persistence ----------
DATA_FILE = Path(__file__).parent / "calendar_data.json"

def load_data():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            # Backward-compat: if old flat structure, wrap into stations
            if "stations" not in data:
                data = {
                    "stations": {
                        "StationA": {
                            "calendar_data": data.get("calendar_data", {}),
                            "calendar_times": data.get("calendar_times", {}),
                            "public_holidays": data.get("public_holidays", {}),
                        },
                        "StationB": {
                            "calendar_data": {},
                            "calendar_times": {},
                            "public_holidays": {},
                        },
                    }
                }
            return data
    # default empty structure
    return {
        "stations": {
            "StationA": {
                "calendar_data": {},
                "calendar_times": {},
                "public_holidays": {},
            },
            "StationB": {
                "calendar_data": {},
                "calendar_times": {},
                "public_holidays": {},
            },
        }
    }

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

store = load_data()

# Helper: ensure station bucket exists and return its dicts
def get_station_bucket(station: str):
    stations = store.setdefault("stations", {})
    if station not in stations:
        stations[station] = {
            "calendar_data": {},
            "calendar_times": {},
            "public_holidays": {},
        }
    bucket = stations[station]
    return bucket["calendar_data"], bucket["calendar_times"], bucket["public_holidays"]

# ---------- Enable CORS for frontend ----------
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

# ---------- Routes ----------
@app.get("/")
def root():
    return {"message": "Backend running"}

@app.get("/calendar")
def get_calendar_data(station: str = Query("StationA")):
    calendar_data, calendar_times, public_holidays = get_station_bucket(station)
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

@app.post("/admin/reset")
def reset_station(station: str = Query("StationA"), secret: str = Query("admReset")):
    # Optional minimal guard. Set RENDER_RESET_SECRET in Render env, e.g., a random string.
    expected = os.environ.get("RENDER_RESET_SECRET", "")
    if expected and secret != expected:
        raise HTTPException(status_code=403, detail="Forbidden")

    cd, ct, ph = get_station_bucket(station)
    cd.clear()
    ct.clear()
    ph.clear()
    save_data(store)
    return {"success": True, "message": f"[{station}] wiped successfully."}

# Add/Update a single date (Working/Holiday) + optional time
@app.post("/calendar")
async def add_calendar_entry(
    request: Request,
    station: str = Query("StationA"),
):
    body = await request.json()
    date = body.get("date")
    status = body.get("status")
    time = (body.get("time") or "").strip()

    if not date or status not in ["Working", "Holiday"]:
        return {"success": False, "message": "Invalid input"}

    calendar_data, calendar_times, public_holidays = get_station_bucket(station)
    calendar_data[date] = status

    # Defaults
    if not time:
        if status == "Working":
            calendar_times[date] = "0 hours"
        else:
            calendar_times[date] = "RD"
    else:
        calendar_times[date] = time

    save_data(store)
    return {"success": True, "message": f"[{station}] Added {status} for {date}"}

# Add a single public holiday
@app.post("/calendar/public")
async def add_public_holiday(
    request: Request,
    station: str = Query("StationA"),
):
    """
    Body: { "date": "YYYY-MM-DD", "name": "Holiday Name" }
    """
    body = await request.json()
    date = (body.get("date") or "").strip()
    name = (body.get("name") or "").strip()

    if not date or not name:
        return {"success": False, "message": "Missing date or name"}

    calendar_data, calendar_times, public_holidays = get_station_bucket(station)
    public_holidays[date] = name
    calendar_data[date] = "Holiday"
    calendar_times[date] = "RD"

    save_data(store)
    return {"success": True, "message": f"[{station}] Added public holiday {name} on {date}"}

# Remove a date (work/holiday/time) from a station
@app.delete("/calendar/{date}")
def remove_calendar_date(
    date: str,
    station: str = Query("StationA"),
):
    calendar_data, calendar_times, public_holidays = get_station_bucket(station)

    removed = False
    if date in calendar_data:
        del calendar_data[date]
        removed = True
    if date in calendar_times:
        del calendar_times[date]
        removed = True

    if removed:
        save_data(store)
        return {"success": True, "message": f"[{station}] Deleted {date}"}
    return {"success": False, "message": "Date not found"}

# Bulk-load AU (QLD) public holidays for a given year into a station
@app.post("/calendar/public/auto")
async def fetch_qld_public_holidays(
    station: str = Query("StationA"),
    year: int = Query(2025),
):
    url = f"https://date.nager.at/api/v3/PublicHolidays/{year}/AU"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=20)
            response.raise_for_status()
            data = response.json()

        calendar_data, calendar_times, public_holidays = get_station_bucket(station)

        count = 0
        for item in data:
            counties = item.get("counties") or []
            # Only keep QLD-specific holidays (some holidays may have no counties -> national; include them if you want)
            if ("QLD" in "".join(counties)) or not counties:
                date = item["date"]  # YYYY-MM-DD
                public_holidays[date] = item["localName"]
                calendar_data[date] = "Holiday"
                calendar_times[date] = "RD"
                count += 1

        save_data(store)
        return {"success": True, "message": f"[{station}] {count} holidays loaded for {year}."}

    except Exception as e:
        return {"success": False, "error": str(e)}

# Remove a single public holiday date from a station
@app.delete("/calendar/public/{date}")
def remove_public_holiday(
    date: str,
    station: str = Query("StationA"),
):
    calendar_data, calendar_times, public_holidays = get_station_bucket(station)

    if date in public_holidays:
        del public_holidays[date]
        calendar_data.pop(date, None)
        calendar_times.pop(date, None)

        save_data(store)
        return {"success": True, "message": f"[{station}] Removed public holiday for {date}"}
    return {"success": False, "message": "Date not found in public holidays"}
