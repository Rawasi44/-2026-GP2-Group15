import calendar as pycalendar
from datetime import date

from flask import (
    Blueprint,
    render_template,
    redirect,
    url_for,
    flash,
    request
)

from flask_login import (
    login_required,
    current_user
)

from extensions import db
from models.event import Event
from models.calendar_event import CalendarEvent


calendar_bp = Blueprint(
    "calendar",
    __name__
)


# ============================================================
# HELPERS
# ============================================================

def get_allowed_attendance_range(event):
    """
    Returns the earliest and latest date the user may
    choose for this event.

    The earliest allowed date is:
    - the event start date, if the event has not started yet
    - today, if the event has already started

    This prevents users from adding past dates.
    """

    today = date.today()

    event_start = event.start_date
    event_end = (
        event.end_date
        or event.start_date
    )

    earliest_allowed = max(
        event_start,
        today
    )

    return (
        earliest_allowed,
        event_end
    )


def validate_scheduled_date(
    event,
    scheduled_date_value
):
    """
    Validates a date selected for the personal calendar.

    Returns:
        (scheduled_date, error_message)

    If valid:
        error_message is None.
    """

    if not scheduled_date_value:

        return (
            None,
            "Please choose the date you plan to attend this event."
        )

    try:

        scheduled_date = (
            date.fromisoformat(
                scheduled_date_value
            )
        )

    except ValueError:

        return (
            None,
            "Please choose a valid date."
        )

    today = date.today()

    # Never allow a past date.
    if scheduled_date < today:

        return (
            None,
            "You cannot add an event to a past date."
        )

    earliest_allowed, event_end = (
        get_allowed_attendance_range(
            event
        )
    )

    # The event itself may already be over.
    if event_end < today:

        return (
            None,
            "This event has already ended and cannot be added to your calendar."
        )

    # Date must also be inside the event's
    # actual available date range.
    if (
        scheduled_date < earliest_allowed
        or
        scheduled_date > event_end
    ):

        return (
            None,
            "The selected date must be within the event's available dates."
        )

    return (
        scheduled_date,
        None
    )


# ============================================================
# PERSONAL CALENDAR PAGE
# ============================================================

@calendar_bp.route("/calendar")
@login_required
def calendar_page():

    today = date.today()

    # Show the requested month/year.
    # If none are provided, show the current month.
    try:

        year = int(
            request.args.get(
                "year",
                today.year
            )
        )

        month = int(
            request.args.get(
                "month",
                today.month
            )
        )

        if month < 1 or month > 12:
            raise ValueError

    except (
        TypeError,
        ValueError
    ):

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


    first_day = date(
        year,
        month,
        1
    )

    next_month_first_day = date(
        next_year,
        next_month,
        1
    )


    # Get the user's planned events
    # for the selected month.
    entries = (
        CalendarEvent.query
        .filter(
            CalendarEvent.user_id
            == current_user.user_id
        )
        .join(
            Event,
            CalendarEvent.event_id
            == Event.id
        )
        .filter(
            Event.is_active == True,
            CalendarEvent.scheduled_date
            >= first_day,
            CalendarEvent.scheduled_date
            < next_month_first_day
        )
        .order_by(
            CalendarEvent.scheduled_date.asc(),
            Event.time.asc(),
            Event.title.asc()
        )
        .all()
    )


    # Group events by the attendance
    # date selected by the user.
    events_by_date = {}

    for entry in entries:

        events_by_date.setdefault(
            entry.scheduled_date,
            []
        ).append(
            entry.event
        )


    # Build a Sunday-to-Saturday
    # monthly calendar grid.
    calendar_builder = (
        pycalendar.Calendar(
            firstweekday=6
        )
    )

    calendar_weeks = []

    for week in (
        calendar_builder
        .monthdatescalendar(
            year,
            month
        )
    ):

        week_data = []

        for day in week:

            week_data.append({
                "date": day,
                "day_number": day.day,
                "in_current_month":
                    day.month == month,
                "is_today":
                    day == today,
                "events":
                    events_by_date.get(
                        day,
                        []
                    )
            })

        calendar_weeks.append(
            week_data
        )


    return render_template(
        "calendar.html",
        calendar_weeks=calendar_weeks,
        month=month,
        year=year,
        month_name=(
            pycalendar
            .month_name[month]
        ),
        prev_month=prev_month,
        prev_year=prev_year,
        next_month=next_month,
        next_year=next_year,
        today=today
    )


# ============================================================
# ADD / REMOVE EVENT
# ============================================================

@calendar_bp.route(
    "/calendar/<int:event_id>/toggle",
    methods=["POST"]
)
@login_required
def toggle_calendar(event_id):

    event = (
        Event.query
        .get_or_404(
            event_id
        )
    )

    existing = (
        CalendarEvent.query
        .filter_by(
            user_id=(
                current_user.user_id
            ),
            event_id=event_id
        )
        .first()
    )

    next_url = (
        request.form.get(
            "next"
        )
        or
        url_for(
            "events.event_details",
            event_id=event_id
        )
    )


    # --------------------------------------------------------
    # REMOVE
    # --------------------------------------------------------

    if existing:

        db.session.delete(
            existing
        )

        db.session.commit()

        flash(
            "Removed from your personal calendar.",
            "success"
        )

        return redirect(
            next_url
        )


    # --------------------------------------------------------
    # ADD
    # --------------------------------------------------------

    # Do not allow adding
    # inactive events.
    if not event.is_active:

        flash(
            (
                "This event is no longer available "
                "and can't be added to your calendar."
            ),
            "warning"
        )

        return redirect(
            request.form.get(
                "next"
            )
            or
            url_for(
                "events.events_page"
            )
        )


    scheduled_date_value = (
        request.form.get(
            "scheduled_date",
            ""
        )
        .strip()
    )


    scheduled_date, error = (
        validate_scheduled_date(
            event,
            scheduled_date_value
        )
    )


    if error:

        flash(
            error,
            "warning"
        )

        return redirect(
            next_url
        )


    # Add the event using the
    # user's selected attendance date.
    new_calendar_entry = (
        CalendarEvent(
            user_id=(
                current_user.user_id
            ),
            event_id=event_id,
            scheduled_date=(
                scheduled_date
            )
        )
    )

    db.session.add(
        new_calendar_entry
    )

    db.session.commit()

    flash(
        "Added to your personal calendar.",
        "success"
    )

    return redirect(
        next_url
    )


# ============================================================
# EDIT ATTENDANCE DATE
# ============================================================

@calendar_bp.route(
    "/calendar/<int:event_id>/update-date",
    methods=["POST"]
)
@login_required
def update_calendar_date(event_id):

    event = (
        Event.query
        .get_or_404(
            event_id
        )
    )


    calendar_entry = (
        CalendarEvent.query
        .filter_by(
            user_id=(
                current_user.user_id
            ),
            event_id=event_id
        )
        .first()
    )


    next_url = (
        request.form.get(
            "next"
        )
        or
        url_for(
            "events.event_details",
            event_id=event_id
        )
    )


    # The event must already
    # exist in this user's calendar.
    if not calendar_entry:

        flash(
            (
                "This event is not currently "
                "in your personal calendar."
            ),
            "warning"
        )

        return redirect(
            next_url
        )


    # Do not allow rescheduling
    # an inactive event.
    if not event.is_active:

        flash(
            (
                "This event is no longer available "
                "and its date cannot be changed."
            ),
            "warning"
        )

        return redirect(
            next_url
        )


    scheduled_date_value = (
        request.form.get(
            "scheduled_date",
            ""
        )
        .strip()
    )


    scheduled_date, error = (
        validate_scheduled_date(
            event,
            scheduled_date_value
        )
    )


    if error:

        flash(
            error,
            "warning"
        )

        return redirect(
            next_url
        )


    # No actual change.
    if (
        calendar_entry.scheduled_date
        == scheduled_date
    ):

        flash(
            (
                "This event is already scheduled "
                "for the selected date."
            ),
            "warning"
        )

        return redirect(
            next_url
        )


    # Update the attendance date.
    calendar_entry.scheduled_date = (
        scheduled_date
    )

    db.session.commit()

    flash(
        (
            "Your calendar date was "
            "updated successfully."
        ),
        "success"
    )

    return redirect(
        next_url
    )
