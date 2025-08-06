import os
import json
import httpx
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# File path for storing persistent data
DATA_FILE = Path(os.path.dirname(__file__)) / "calendar_data.json"

def load_data():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {
        "calendar_data": {},
        "calendar_times": {},
        "public_holidays": {}
    }

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

# Load initial data
store = load_data()
calendar_data = store["calendar_data"]
calendar_times = store["calendar_times"]
public_holidays = store["public_holidays"]

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
                    "https://employee-calendar-frontend.vercel.app",
                    "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"message": "Backend running"}

@app.get("/calendar")
def get_calendar_data():
    return {
        "calendar_data": calendar_data,
        "public_holidays": public_holidays,
        "calendar_times": calendar_times
    }

@app.post("/login")
async def login(request: Request):
    data = await request.json()
    email = data.get("email")
    password = data.get("password")

    if email == "franklin@gmail.com" and password == "secret":
        return {"success": True, "message": "Login successful"}
    else:
        return {"success": False, "message": "Invalid credentials"}

@app.post("/calendar")
async def add_calendar_entry(request: Request):
    data = await request.json()
    date = data.get("date")
    status = data.get("status")
    time = data.get("time", "").strip()

    if not date or status not in ["Working", "Holiday"]:
        return {"success": False, "message": "Invalid input"}

    calendar_data[date] = status

    # Apply default time
    if not time:
        if status == "Working":
            calendar_times[date] = "0 hours"
        elif status == "Holiday":
            calendar_times[date] = "Off"
    else:
        calendar_times[date] = time

    save_data({
        "calendar_data": calendar_data,
        "calendar_times": calendar_times,
        "public_holidays": public_holidays
    })

    return {"success": True, "message": f"Added {status} for {date}"}

@app.delete("/calendar/{date}")
def remove_calendar_date(date: str):
    removed = False

    if date in calendar_data:
        del calendar_data[date]
        removed = True

    if date in calendar_times:
        del calendar_times[date]
        removed = True

    if removed:
        save_data({
            "calendar_data": calendar_data,
            "calendar_times": calendar_times,
            "public_holidays": public_holidays
        })
        return {"success": True, "message": f"Deleted {date}"}
    return {"success": False, "message": "Date not found"}

@app.post("/calendar/public/auto")
async def fetch_qld_public_holidays():
    year = 2025
    url = f"https://date.nager.at/api/v3/PublicHolidays/{year}/AU"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            data = response.json()

            count = 0
            for item in data:
                counties = item.get("counties") or []
                if any("QLD" in c for c in counties):  # Filters only QLD holidays
                    date = item["date"]
                    public_holidays[date] = item["localName"]
                    calendar_data[date] = "Holiday"
                    calendar_times[date] = "Off"
                    count += 1

            save_data({
                "calendar_data": calendar_data,
                "calendar_times": calendar_times,
                "public_holidays": public_holidays
            })

        return {"success": True, "message": f"{count} QLD holidays loaded."}

    except Exception as e:
        return {"success": False, "error": str(e)}

@app.delete("/calendar/public/{date}")
def remove_public_holiday(date: str):
    if date in public_holidays:
        del public_holidays[date]
        calendar_data.pop(date, None)
        calendar_times.pop(date, None)

        save_data({
            "calendar_data": calendar_data,
            "calendar_times": calendar_times,
            "public_holidays": public_holidays
        })

        return {"success": True, "message": f"Removed public holiday for {date}"}
    return {"success": False, "message": "Date not found in public holidays"}
