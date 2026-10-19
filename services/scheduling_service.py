"""AFAQ Smart Schedule, built on Sprint 4's content-based recommendations.

The Smart Schedule is a proposal only. It suggests possible event visits
based on the user's interests, selected dates, preferred time, and event
availability.

Suggested visit times are planning estimates inside the event's listed
opening hours. They are not official bookings or confirmed session times.

The user decides which individual suggestions to save to the Personal
Calendar.
"""

import re
from collections import Counter
from datetime import date, timedelta

from models.calendar_event import CalendarEvent
from models.event import Event
from services.recommendation_service import get_recommendations_for_user


MAX_HORIZON_DAYS = 90

VISIT_MINUTES = 90
BUFFER_MINUTES = 30

# The numbered choices available in the interface.
# "Any" is handled separately as None.
MAX_EVENTS_PER_DAY = 3


# Local, date-bound planning windows,
# measured in minutes since midnight.
TIME_WINDOWS = {
    "morning": (8 * 60, 12 * 60),
    "afternoon": (12 * 60, 17 * 60),
    "evening": (17 * 60, 24 * 60),
    "any": (8 * 60, 24 * 60),
}


# Examples in AFAQ's catalog:
# "16:00 - 23:59"
# "16:00 - 00:45"
_TIME_PATTERN = re.compile(
    r"^\s*(\d{1,2}):(\d{2})\s*([AaPp][Mm])?\s*"
    r"(?:-|–|—|to)\s*"
    r"(\d{1,2}):(\d{2})\s*([AaPp][Mm])?\s*$"
)

_SINGLE_TIME_PATTERN = re.compile(
    r"^\s*(\d{1,2}):(\d{2})\s*([AaPp][Mm])?\s*$"
)


def _minutes(hours, minutes, ampm=None):
    """Convert a clock time to minutes since midnight."""
    if minutes > 59 or hours > 24 or hours < 0:
        return None

    if ampm:
        if not 1 <= hours <= 12:
            return None

        hours = (
            hours % 12
            + (
                12
                if ampm.upper() == "PM"
                else 0
            )
        )

    elif hours == 24 and minutes != 0:
        return None

    return hours * 60 + minutes


def parse_event_hours(raw):
    """
    Return the event opening interval as:

        (start_minutes, end_minutes)

    or None if the value cannot be interpreted safely.

    If the end time is earlier than the start time,
    it is treated as a next-day closing time.
    """

    if not raw or not isinstance(raw, str):
        return None

    text = raw.strip()

    match = _TIME_PATTERN.fullmatch(text)

    if match:
        (
            h1,
            m1,
            ap1,
            h2,
            m2,
            ap2,
        ) = match.groups()

        start = _minutes(
            int(h1),
            int(m1),
            ap1,
        )

        end = _minutes(
            int(h2),
            int(m2),
            ap2,
        )

        if (
            start is None
            or end is None
            or start >= 24 * 60
        ):
            return None

        if end <= start:
            end += 24 * 60

        return start, end

    single = _SINGLE_TIME_PATTERN.fullmatch(
        text
    )

    if single:
        h, m, ampm = single.groups()

        start = _minutes(
            int(h),
            int(m),
            ampm,
        )

        if (
            start is not None
            and start < 24 * 60
        ):
            return (
                start,
                start + VISIT_MINUTES,
            )

    return None


def _display_time(minutes):
    """Format minutes since midnight as HH:MM."""
    return (
        f"{minutes // 60:02d}:"
        f"{minutes % 60:02d}"
    )


def _available_on(event, planned_date):
    """
    Return True when an active event is available
    on the user's selected date.
    """

    event_end = (
        event.end_date
        or event.start_date
    )

    return bool(
        event.is_active
        and event.start_date
        <= planned_date
        <= event_end
    )


def _find_visit_slot(
    event_hours,
    window,
    busy,
):
    """
    Find a possible 90-minute visit slot.

    A 30-minute buffer is kept between suggested
    visits to avoid producing an unrealistic schedule.
    """

    start = max(
        event_hours[0],
        window[0],
    )

    stop = min(
        event_hours[1],
        window[1],
        24 * 60,
    )

    for proposed in range(
        start,
        stop - VISIT_MINUTES + 1,
        15,
    ):
        end = (
            proposed
            + VISIT_MINUTES
        )

        no_conflict = all(
            (
                end + BUFFER_MINUTES
                <= taken_start
            )
            or (
                proposed
                >= taken_end
                + BUFFER_MINUTES
            )
            for (
                taken_start,
                taken_end,
            ) in busy
        )

        if no_conflict:
            return proposed, end

    return None


def validate_planning_request(
    raw_dates,
    time_window,
    count,
    today=None,
):
    """
    Validate the Smart Schedule request.

    Returns:
        selected_dates,
        max_events_per_day

    max_events_per_day is:
        int -> when the user chooses 1, 2, or 3
        None -> when the user chooses "Any"

    There is no fixed limit on how many dates the user
    can select. Dates only need to fall within the
    allowed 90-day planning horizon.
    """

    today = (
        today
        or date.today()
    )

    # -----------------------------
    # Validate preferred time
    # -----------------------------
    if time_window not in TIME_WINDOWS:
        raise ValueError(
            "Please choose a valid preferred time."
        )

    # -----------------------------
    # Validate events-per-day choice
    # -----------------------------
    if (
        count is None
        or str(count).strip().lower()
        == "any"
    ):
        count = None

    else:
        try:
            count = int(count)

        except (
            TypeError,
            ValueError,
        ):
            raise ValueError(
                "Please choose a valid number of events per day."
            ) from None

        if not (
            1
            <= count
            <= MAX_EVENTS_PER_DAY
        ):
            raise ValueError(
                "Please choose 1, 2, 3, or Any events per day."
            )

    # -----------------------------
    # At least one date is required
    # -----------------------------
    if not raw_dates:
        raise ValueError(
            "Please choose at least one available date."
        )

    selected = []

    # -----------------------------
    # Validate every selected date
    # -----------------------------
    for raw in raw_dates:
        try:
            day = date.fromisoformat(
                raw
            )

        except (
            TypeError,
            ValueError,
        ):
            raise ValueError(
                "Please select valid dates."
            ) from None

        if not (
            today
            <= day
            <= today
            + timedelta(
                days=MAX_HORIZON_DAYS
            )
        ):
            raise ValueError(
                "Choose dates within the next 90 days."
            )

        if day in selected:
            raise ValueError(
                "Please do not select the same day twice."
            )

        selected.append(
            day
        )

    return (
        sorted(selected),
        count,
    )


def generate_smart_schedule(
    user_id,
    selected_days,
    preferred_time="any",
    max_per_day=2,
):
    """
    Generate personalized Smart Schedule suggestions.

    This function does NOT save anything to the database.

    The user decides which individual suggested events
    to save later.

    max_per_day:
        1 / 2 / 3 -> limit suggestions per selected day
        None      -> "Any", with no numeric daily cap

    Even when "Any" is selected, suggestions must still:
    - match the selected date
    - match the preferred time
    - have usable event-time information
    - fit into a non-overlapping suggested visit slot
    - be active
    - not already exist in the user's Personal Calendar
    """

    if not user_id:
        return []

    (
        selected_days,
        max_per_day,
    ) = validate_planning_request(
        [
            day.isoformat()
            for day in selected_days
        ],
        preferred_time,
        max_per_day,
    )

    active_events = Event.query.filter(
        Event.is_active == True
    ).all()

    if not active_events:
        return [
            {
                "date": day,
                "items": [],
                "existing_count": 0,
            }
            for day in selected_days
        ]

    # ---------------------------------------------------------
    # Existing Personal Calendar entries
    # ---------------------------------------------------------
    existing_entries = (
        CalendarEvent.query
        .filter_by(
            user_id=user_id
        )
        .all()
    )

    existing_ids = {
        entry.event_id
        for entry
        in existing_entries
    }

    saved_on = Counter(
        entry.scheduled_date
        for entry
        in existing_entries
    )

    # ---------------------------------------------------------
    # Sprint 4 Recommendation System provides the
    # personalized relevance ranking.
    #
    # Request the complete active catalog because the
    # highest-ranked events might not be available on
    # the user's selected dates.
    # ---------------------------------------------------------
    ranked = (
        get_recommendations_for_user(
            user_id,
            limit=len(active_events),
        )
    )

    active_by_id = {
        event.id: event
        for event
        in active_events
    }

    eligible = [
        active_by_id[event.id]
        for event
        in ranked
        if (
            event.id
            in active_by_id
            and event.id
            not in existing_ids
        )
    ]

    used_ids = set()

    generated = []

    # ---------------------------------------------------------
    # Build suggestions for every selected day
    # ---------------------------------------------------------
    for day in selected_days:
        busy = []
        items = []

        existing_count = (
            saved_on.get(
                day,
                0,
            )
        )

        # If the user selected "Any", there is no numeric
        # maximum for this day.
        if max_per_day is None:
            remaining = None

        else:
            remaining = max(
                0,
                max_per_day
                - existing_count,
            )

        for event in eligible:

            # Stop only when a numeric maximum was chosen.
            if (
                remaining is not None
                and len(items)
                >= remaining
            ):
                break

            if event.id in used_ids:
                continue

            if not _available_on(
                event,
                day,
            ):
                continue

            hours = parse_event_hours(
                event.time
            )

            if hours is None:
                # Do not invent hours when the event's
                # actual time information is unclear.
                continue

            slot = _find_visit_slot(
                hours,
                TIME_WINDOWS[
                    preferred_time
                ],
                busy,
            )

            if slot is None:
                continue

            start, end = slot

            items.append(
                {
                    "event": event,
                    "event_id": event.id,
                    "date": day,
                    "suggested_start": (
                        _display_time(
                            start
                        )
                    ),
                    "suggested_end": (
                        _display_time(
                            end
                        )
                    ),
                    "listed_hours": (
                        event.time
                    ),
                }
            )

            busy.append(
                slot
            )

            used_ids.add(
                event.id
            )

        generated.append(
            {
                "date": day,
                "items": items,
                "existing_count": (
                    existing_count
                ),
            }
        )

    return generated