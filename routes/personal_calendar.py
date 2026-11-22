import calendar as pycalendar
from datetime import date, time

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
from services.scheduling_service import (
    find_calendar_time_conflict,
    minutes_from_time,
    parse_event_hours,
)


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


    # Group calendar ENTRIES (not just the bare Event) by the
    # attendance date selected by the user, so the template can show
    # the saved attendance_start_time/end_time (Phase 1.7) alongside
    # the event itself.
    events_by_date = {}

    for entry in entries:

        events_by_date.setdefault(
            entry.scheduled_date,
            []
        ).append(
            entry
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


# ============================================================
# EDIT ATTENDANCE TIME (Phase 1.6 / 1.7)
#
# Requires migrations/0002_add_calendar_attendance_times.sql.
# ============================================================

def validate_attendance_time(
    event,
    calendar_entry,
    start_value,
    end_value,
):
    """
    Validates an edited attendance time for an existing Personal
    Calendar entry, honoring the three possible cases (Phase 1.6
    correction):

      fully official  (start_time_is_estimated=False, end=False)
        -> cannot be edited here at all.
      mixed           (start=False, end=True)
        -> the official start can never move; only the estimated END
           may be changed.
      fully estimated (start=True, end=True)
        -> both start and end may be changed, within the event's own
           listed hours when known.

    Returns (start_time_or_None, end_time_or_None,
             new_start_is_estimated, new_end_is_estimated,
             error_message).
    error_message is None when valid. On success the three
    not-an-error return values are exactly what should be written to
    the CalendarEvent row.
    """

    start_is_est = calendar_entry.start_time_is_estimated
    end_is_est = calendar_entry.end_time_is_estimated

    fully_official = start_is_est is False and end_is_est is False
    mixed = start_is_est is False and end_is_est is True

    if fully_official:
        return (
            None,
            None,
            None,
            None,
            (
                "This event has a verified official time and "
                "cannot be rescheduled to a different time here."
            ),
        )

    # Clearing both fields is allowed for mixed/estimated entries --
    # falls back to date-only.
    if not start_value and not end_value:
        return None, None, None, None, None

    if mixed:
        # The official start can never move. Only the estimated end
        # may be edited; any start_value supplied is ignored rather
        # than trusted, so the official start can't be overwritten by
        # mistake or by a tampered request.
        if not end_value:
            return (
                None,
                None,
                None,
                None,
                "Please provide an end time, or leave it empty to clear the saved time.",
            )

        try:
            end_t = time.fromisoformat(end_value)
        except (TypeError, ValueError):
            return None, None, None, None, "Please provide a valid end time."

        start_t = calendar_entry.attendance_start_time
        start_minutes = start_t.hour * 60 + start_t.minute
        end_minutes = end_t.hour * 60 + end_t.minute

        if end_minutes <= start_minutes:
            return None, None, None, None, "End time must be after the official start time."

        conflict = find_calendar_time_conflict(
            calendar_entry.user_id,
            calendar_entry.scheduled_date,
            start_minutes,
            end_minutes,
            exclude_calendar_entry_id=calendar_entry.id,
        )

        if conflict:
            return (
                None,
                None,
                None,
                None,
                f'This time conflicts with "{conflict.event.title}" '
                "already on your calendar that day.",
            )

        # start_is_estimated stays False -- the official start is
        # untouched; only the end was re-estimated by the user.
        return start_t, end_t, False, True, None

    # ------------------------------------------------------------
    # Fully estimated: both start and end may be edited.
    # ------------------------------------------------------------
    if not start_value or not end_value:
        return (
            None,
            None,
            None,
            None,
            "Please provide both a start and an end time, or leave both empty.",
        )

    try:
        start_t = time.fromisoformat(start_value)
        end_t = time.fromisoformat(end_value)
    except (TypeError, ValueError):
        return None, None, None, None, "Please provide a valid start and end time."

    start_minutes = start_t.hour * 60 + start_t.minute
    end_minutes = end_t.hour * 60 + end_t.minute

    if end_minutes <= start_minutes:
        return None, None, None, None, "End time must be after start time."

    # Must stay within the event's own listed hours, when those
    # hours are actually known -- never silently widen past them.
    hours = parse_event_hours(event.time)

    if hours:
        event_start, event_end = hours

        if start_minutes < event_start:
            return (
                None,
                None,
                None,
                None,
                "The selected start time is before this event's listed hours.",
            )

        if event_end is not None and end_minutes > event_end:
            return (
                None,
                None,
                None,
                None,
                "The selected end time is after this event's listed hours.",
            )

    # Revalidate against the user's OTHER saved entries that day.
    conflict = find_calendar_time_conflict(
        calendar_entry.user_id,
        calendar_entry.scheduled_date,
        start_minutes,
        end_minutes,
        exclude_calendar_entry_id=calendar_entry.id,
    )

    if conflict:
        return (
            None,
            None,
            None,
            None,
            f'This time conflicts with "{conflict.event.title}" '
            "already on your calendar that day.",
        )

    return start_t, end_t, True, True, None


@calendar_bp.route(
    "/calendar/<int:event_id>/update-time",
    methods=["POST"],
)
@login_required
def update_calendar_time(event_id):

    event = (
        Event.query
        .get_or_404(event_id)
    )

    calendar_entry = (
        CalendarEvent.query
        .filter_by(
            user_id=current_user.user_id,
            event_id=event_id,
        )
        .first()
    )

    next_url = (
        request.form.get("next")
        or url_for(
            "events.event_details",
            event_id=event_id,
        )
    )

    if not calendar_entry:
        flash(
            "This event is not currently in your personal calendar.",
            "warning",
        )
        return redirect(next_url)

    if not event.is_active:
        flash(
            "This event is no longer available and its time cannot be changed.",
            "warning",
        )
        return redirect(next_url)

    start_value = request.form.get("attendance_start_time", "").strip()
    end_value = request.form.get("attendance_end_time", "").strip()

    (
        start_t,
        end_t,
        new_start_is_estimated,
        new_end_is_estimated,
        error,
    ) = validate_attendance_time(
        event,
        calendar_entry,
        start_value,
        end_value,
    )

    if error:
        flash(error, "warning")
        return redirect(next_url)

    calendar_entry.attendance_start_time = start_t
    calendar_entry.attendance_end_time = end_t
    calendar_entry.start_time_is_estimated = new_start_is_estimated
    calendar_entry.end_time_is_estimated = new_end_is_estimated

    db.session.commit()

    flash(
        "Your attendance time was updated." if start_t else "Attendance time cleared.",
        "success",
    )

    return redirect(next_url)
