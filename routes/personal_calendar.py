import calendar as pycalendar
from datetime import date

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from extensions import db
from models.event import Event
from models.calendar_event import CalendarEvent


calendar_bp = Blueprint("calendar", __name__)


@calendar_bp.route("/calendar")
@login_required
def calendar_page():
    today = date.today()

    # Show the requested month/year.
    # If none are provided, show the current month.
    try:
        year = int(request.args.get("year", today.year))
        month = int(request.args.get("month", today.month))

        if month < 1 or month > 12:
            raise ValueError

    except (TypeError, ValueError):
        year = today.year
        month = today.month

    # Previous month
    if month == 1:
        prev_month = 12
        prev_year = year - 1
    else:
        prev_month = month - 1
        prev_year = year

    # Next month
    if month == 12:
        next_month = 1
        next_year = year + 1
    else:
        next_month = month + 1
        next_year = year

    first_day = date(year, month, 1)
    next_month_first_day = date(next_year, next_month, 1)

    # Get the user's planned events for the selected month.
    # The calendar now uses CalendarEvent.scheduled_date instead of
    # Event.start_date, because the user chooses the date they plan
    # to attend the event.
    entries = (
        CalendarEvent.query
        .filter(CalendarEvent.user_id == current_user.user_id)
        .join(Event, CalendarEvent.event_id == Event.id)
        .filter(
            Event.is_active == True,
            CalendarEvent.scheduled_date >= first_day,
            CalendarEvent.scheduled_date < next_month_first_day
        )
        .order_by(
            CalendarEvent.scheduled_date.asc(),
            Event.time.asc(),
            Event.title.asc()
        )
        .all()
    )

    # Group events by the date selected by the user.
    events_by_date = {}

    for entry in entries:
        events_by_date.setdefault(
            entry.scheduled_date,
            []
        ).append(entry.event)

    # Build a Sunday-to-Saturday monthly calendar grid.
    calendar_builder = pycalendar.Calendar(firstweekday=6)

    calendar_weeks = []

    for week in calendar_builder.monthdatescalendar(year, month):
        week_data = []

        for day in week:
            week_data.append({
                "date": day,
                "day_number": day.day,
                "in_current_month": day.month == month,
                "is_today": day == today,
                "events": events_by_date.get(day, [])
            })

        calendar_weeks.append(week_data)

    return render_template(
        "calendar.html",
        calendar_weeks=calendar_weeks,
        month=month,
        year=year,
        month_name=pycalendar.month_name[month],
        prev_month=prev_month,
        prev_year=prev_year,
        next_month=next_month,
        next_year=next_year,
        today=today
    )


@calendar_bp.route("/calendar/<int:event_id>/toggle", methods=["POST"])
@login_required
def toggle_calendar(event_id):
    event = Event.query.get_or_404(event_id)

    existing = CalendarEvent.query.filter_by(
        user_id=current_user.user_id,
        event_id=event_id
    ).first()

    next_url = (
        request.form.get("next")
        or url_for(
            "events.event_details",
            event_id=event_id
        )
    )

    # If already in the calendar, remove it.
    if existing:
        db.session.delete(existing)
        db.session.commit()

        flash(
            "Removed from your personal calendar.",
            "success"
        )

        return redirect(next_url)

    # Do not allow adding inactive events.
    if not event.is_active:
        flash(
            "This event is no longer available and can't be added to your calendar.",
            "warning"
        )

        return redirect(
            request.form.get("next")
            or url_for("events.events_page")
        )

    # Read the date selected by the user.
    scheduled_date_value = request.form.get(
        "scheduled_date",
        ""
    ).strip()

    if not scheduled_date_value:
        flash(
            "Please choose the date you plan to attend this event.",
            "warning"
        )
        return redirect(next_url)

    try:
        scheduled_date = date.fromisoformat(
            scheduled_date_value
        )
    except ValueError:
        flash(
            "Please choose a valid date.",
            "danger"
        )
        return redirect(next_url)

    # The selected attendance date must fall within
    # the event's available date range.
    event_start = event.start_date
    event_end = event.end_date or event.start_date

    if scheduled_date < event_start or scheduled_date > event_end:
        flash(
            "The selected date must be within the event's available dates.",
            "warning"
        )
        return redirect(next_url)

    # Add the event using the user's selected attendance date.
    new_calendar_entry = CalendarEvent(
        user_id=current_user.user_id,
        event_id=event_id,
        scheduled_date=scheduled_date
    )

    db.session.add(new_calendar_entry)
    db.session.commit()

    flash(
        "Added to your personal calendar.",
        "success"
    )

    return redirect(next_url)
