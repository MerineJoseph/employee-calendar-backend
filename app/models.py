# app/models.py
from sqlalchemy import String, Date, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base

class CalendarEntry(Base):
    __tablename__ = "calendar_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    station: Mapped[str] = mapped_column(String(32), index=True)      # "StationA" / "StationB"
    date: Mapped[Date] = mapped_column(index=True)                    # YYYY-MM-DD
    status: Mapped[str] = mapped_column(String(16))                   # "Working" / "Holiday"
    time_label: Mapped[str | None] = mapped_column(String(64), nullable=True)  # "9:00–17:00" / "RD"
    is_public_holiday: Mapped[bool] = mapped_column(Boolean, default=False)
    holiday_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
