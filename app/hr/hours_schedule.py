"""One schedule for weekly hours.

A work week is Monday–Friday. Weekend hours belong in that week. Hours are due
on the configured deadline day at 11:59 PM America/New_York (Friday by default).
Heads-up is the previous business day at 9:00 AM. The due-day reminder is
8:00 AM on the deadline day. The payroll summary is 8:00 AM on the next
business day. Overdue reminders are 9:00 AM on the next 10 business days,
weekdays only, and only while the hours are still missing.

Weeks before November 16–20, 2026 are not mailed. That keeps the Nov 9–13
week quiet at deploy and prevents a backlog of overdue mail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

FIRST_LIVE_WEEK_ENDING = date(2026, 11, 20)
DEADLINE_TIME = "23:59"
HEADS_UP_TIME = "09:00"
DUE_DAY_TIME = "08:00"
SUMMARY_TIME = "08:00"
OVERDUE_TIME = "09:00"
OVERDUE_BUSINESS_DAYS = 10

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def is_business_day(day: date) -> bool:
    return day.weekday() < 5


def previous_business_day(day: date) -> date:
    cursor = day - timedelta(days=1)
    while not is_business_day(cursor):
        cursor -= timedelta(days=1)
    return cursor


def next_business_day(day: date) -> date:
    cursor = day + timedelta(days=1)
    while not is_business_day(cursor):
        cursor += timedelta(days=1)
    return cursor


def business_days_after(day: date, count: int) -> list[date]:
    """The next `count` weekdays after `day`, not including `day`."""
    found: list[date] = []
    cursor = day
    while len(found) < count:
        cursor += timedelta(days=1)
        if is_business_day(cursor):
            found.append(cursor)
    return found


def at_local(day: date, hhmm: str) -> datetime:
    hour, minute = (int(part) for part in hhmm.split(":", 1))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)


def collection_friday(moment: datetime) -> date:
    """Friday of the Monday–Sunday collection that contains `moment`.

    Monday through Friday use that week's Friday. Saturday and Sunday use the
    Friday that just ended, because weekend work stays in that week.
    """
    local = moment.astimezone(ET).date()
    monday = local - timedelta(days=local.weekday())
    return monday + timedelta(days=4)


def week_monday(week_friday: date) -> date:
    return week_friday - timedelta(days=4)


def deadline_date(week_friday: date, deadline_weekday: int) -> date:
    weekday = min(max(int(deadline_weekday), 0), 4)
    return week_monday(week_friday) + timedelta(days=weekday)


def long_date(day: date) -> str:
    return f"{day.strftime('%A, %B')} {day.day}, {day.year}"


def clock_phrase(hhmm: str) -> str:
    hour, minute = (int(part) for part in hhmm.split(":", 1))
    suffix = "AM" if hour < 12 else "PM"
    hour12 = hour % 12 or 12
    return f"{hour12}:{minute:02d} {suffix}"


@dataclass(frozen=True)
class HoursSchedule:
    week_ending: date
    deadline_weekday: int
    deadline_on: date
    deadline_at: datetime
    heads_up_at: datetime
    due_at: datetime
    summary_at: datetime
    overdue_days: tuple[date, ...]

    @property
    def week_start(self) -> date:
        return week_monday(self.week_ending)

    @property
    def label(self) -> str:
        return f"{long_date(self.week_start)} – {long_date(self.week_ending)}"

    @property
    def summary_day(self) -> date:
        return self.summary_at.astimezone(ET).date()

    @property
    def window_end(self) -> datetime:
        """Employee entry stays open through the last overdue business day."""
        last = self.overdue_days[-1] if self.overdue_days else self.deadline_on
        return at_local(last, DEADLINE_TIME)

    def overdue_at(self, day: date) -> datetime:
        return at_local(day, OVERDUE_TIME)

    def mailable(self) -> bool:
        return self.week_ending >= FIRST_LIVE_WEEK_ENDING


def schedule_for(week_friday: date, deadline_weekday: int = 4) -> HoursSchedule:
    due_on = deadline_date(week_friday, deadline_weekday)
    heads = previous_business_day(due_on)
    summary = next_business_day(due_on)
    overdue = tuple(business_days_after(due_on, OVERDUE_BUSINESS_DAYS))
    return HoursSchedule(
        week_ending=week_friday,
        deadline_weekday=min(max(int(deadline_weekday), 0), 4),
        deadline_on=due_on,
        deadline_at=at_local(due_on, DEADLINE_TIME),
        heads_up_at=at_local(heads, HEADS_UP_TIME),
        due_at=at_local(due_on, DUE_DAY_TIME),
        summary_at=at_local(summary, SUMMARY_TIME),
        overdue_days=overdue,
    )


def schedule_for_moment(moment: datetime, deadline_weekday: int = 4) -> HoursSchedule:
    return schedule_for(collection_friday(moment), deadline_weekday)


def preview_sentence(spec: HoursSchedule, recipient_name: str = "Melissa") -> str:
    """Read-only settings sentence. Times are fixed; only the deadline day moves."""
    heads = spec.heads_up_at.astimezone(ET)
    due = spec.due_at.astimezone(ET)
    summary = spec.summary_at.astimezone(ET)
    who = (recipient_name or "Melissa").split(" ", 1)[0] or "Melissa"
    return (
        f"Heads-up {_WEEKDAYS[heads.weekday()]} {clock_phrase(HEADS_UP_TIME)} · "
        f"Due-today reminder {_WEEKDAYS[due.weekday()]} {clock_phrase(DUE_DAY_TIME)} · "
        f"Due {_WEEKDAYS[spec.deadline_on.weekday()]} {clock_phrase(DEADLINE_TIME)} · "
        f"{who}'s summary {_WEEKDAYS[summary.weekday()]} {clock_phrase(SUMMARY_TIME)} · "
        f"Weekday overdue reminders at {clock_phrase(OVERDUE_TIME)} until submitted, "
        f"up to {OVERDUE_BUSINESS_DAYS} business days"
    )


def on_schedule_minute(now: datetime, scheduled: datetime) -> bool:
    """True only during the scheduled minute. A later tick does not catch up."""
    return scheduled <= now < scheduled + timedelta(minutes=2)
