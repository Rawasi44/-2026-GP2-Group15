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
from services.recommendation_service import (
    explain_recommendations,
    get_recommendations_for_user,
)


MAX_HORIZON_DAYS = 90

BUFFER_MINUTES = 30

# ---------------------------------------------------------------------------
# Adaptive attendance duration (Phase 5.2)
#
# Replaces a single fixed visit length for every event with event-type-aware
# estimates. Classification uses category + title + description keywords --
# deliberately simple, maintainable rules rather than an ML model, which is
# both explainable in a viva and appropriate for AFAQ's catalog size.
#
# AFAQ's Event model has no verified official per-visit duration field (only
# a listed opening-hours string), so every value below is a SUGGESTED VISIT
# DURATION, never presented as an official duration.
#
# Fixed-duration events: the suggested duration approximates how long the
# whole activity itself typically runs (a match, a show, a screening).
# Flexible-duration events: the venue is open far longer than any one
# visit; the suggested duration is how long a typical visit would be.
# ---------------------------------------------------------------------------

FIXED_DURATION_EVENT_TYPES = {
    "football_match": {
        "keywords": ("football", "soccer", "match", "كرة القدم", "مباراة"),
        "minutes": 120,
        "label": "football match",
    },
    "theatre": {
        "keywords": ("theatre", "theater", "play", "مسرح", "مسرحية"),
        "minutes": 120,
        "label": "theatre performance",
    },
    "cinema": {
        "keywords": ("cinema", "movie", "screening", "سينما", "فيلم"),
        "minutes": 120,
        "label": "cinema screening",
    },
    "workshop": {
        "keywords": ("workshop", "ورشة"),
        "minutes": 150,
        "label": "workshop",
    },
    "concert": {
        "keywords": ("concert", "حفل", "حفلة"),
        "minutes": 150,
        "label": "concert",
    },
    "sports_general": {
        "keywords": ("tournament", "دوري", "رياضي"),
        "minutes": 120,
        "label": "sports event",
    },
}

FLEXIBLE_DURATION_EVENT_TYPES = {
    "exhibition": {
        "keywords": ("exhibition", "معرض", "معارض"),
        "minutes": 120,
        "label": "exhibition",
    },
    "festival": {
        "keywords": ("festival", "مهرجان"),
        "minutes": 150,
        "label": "festival",
    },
    "conference": {
        "keywords": ("conference", "مؤتمر"),
        "minutes": 180,
        "label": "conference",
    },
}

DEFAULT_SUGGESTED_VISIT_MINUTES = 90


def classify_event_duration(event):
    """
    Returns (bucket, duration_minutes, type_label).

    bucket is "fixed" or "flexible". When no keyword matches, falls back
    to a flexible "event" bucket with the previous universal default so
    behavior degrades gracefully instead of guessing.
    """
    haystack = " ".join(
        part for part in (event.category, event.title, event.description) if part
    ).lower()

    for info in FIXED_DURATION_EVENT_TYPES.values():
        if any(keyword in haystack for keyword in info["keywords"]):
            return "fixed", info["minutes"], info["label"]

    for info in FLEXIBLE_DURATION_EVENT_TYPES.values():
        if any(keyword in haystack for keyword in info["keywords"]):
            return "flexible", info["minutes"], info["label"]

    return "flexible", DEFAULT_SUGGESTED_VISIT_MINUTES, "event"


def describe_suggested_duration(bucket, minutes, type_label, duration_is_estimated=True):
    """
    Builds the user-facing explanation string (Phase 5.5 / 1.3).

    duration_is_estimated distinguishes an AFAQ guess from a verified
    official session length (a genuine two-time listed range for a
    fixed-bucket event) -- an official duration is never described as
    "suggested".
    """
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        duration_text = f"{hours}h {mins}m"
    elif hours:
        duration_text = f"{hours} hour" if hours == 1 else f"{hours} hours"
    else:
        duration_text = f"{mins} minutes"

    if bucket == "fixed" and not duration_is_estimated:
        return f"Official {duration_text} session length for this {type_label}."

    if bucket == "fixed":
        return (
            f"Estimated {duration_text} visit, based on the official "
            f"start time for this {type_label} (no official end time "
            f"was listed)."
        )

    return f"Suggested {duration_text} visit based on the event type ({type_label})."


# ---------------------------------------------------------------------------
# Phase 1.6 correction: a single "is this time estimated" boolean cannot
# represent a MIXED slot (an official, unmovable start time with an
# AFAQ-estimated end/duration -- e.g. a football match that officially
# starts at 20:00 but has no listed official end, so the end is AFAQ's
# own duration estimate). Two independent flags are tracked instead:
#
#   start_time_is_estimated / end_time_is_estimated
#     False -> that boundary is the event's own verified, listed value.
#     True  -> AFAQ selected or estimated that boundary.
#     None  -> no time was saved at all for this entry.
#
# This lets exactly three real cases be represented, plus "no time
# saved":
#   (False, False) -> fully official
#   (True,  True)  -> fully estimated/flexible
#   (False, True)  -> mixed: official start, estimated end
#   (None,  None)  -> no time saved (date-only)
# ---------------------------------------------------------------------------

def describe_time_basis(start_time_is_estimated, end_time_is_estimated):
    """
    User-facing label for a saved or proposed attendance time, from the
    two independent estimate flags above. Returns None when no time is
    saved (both flags None) -- callers should show "Time to be
    confirmed" or nothing in that case, never a time-basis label.
    """
    if start_time_is_estimated is None or end_time_is_estimated is None:
        return None

    if not start_time_is_estimated and not end_time_is_estimated:
        return "Official time"

    if start_time_is_estimated and end_time_is_estimated:
        return "Suggested attendance time"

    if not start_time_is_estimated and end_time_is_estimated:
        return "Official start • Estimated end"

    # start estimated, end official -- not produced by AFAQ's own
    # scheduling logic today, but handled so no caller ever crashes or
    # mislabels an estimate as official.
    return "Estimated start • Official end"


# Sanity cap for a user-typed "events per day" number. The interface no
# longer limits the user to 1/2/3 -- they can type any number or choose
# "All" -- but an unbounded number is still rejected so a typo (or a
# malicious request) can't ask the planner to try to pack hundreds of
# events into a single day. "All"/"Any" is handled separately as None.
MAX_EVENTS_PER_DAY = 20


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
    Return the event's listed time information as:

        (start_minutes, end_minutes)

    or None if the value cannot be interpreted safely.

    - A real two-time range ("16:00 - 23:59") returns a genuine
      (start, end) pair. If the end time is earlier than the start
      time, it is treated as a next-day closing time.
    - A single declared time ("16:00") returns (start, None). That is
      the event's stated START time only -- NOT a stated end time, so
      no official closing time is invented here. Callers must size the
      suggested visit using the event's own adaptive visit_minutes
      (see classify_event_duration) within the day/preferred-time
      window instead of a fabricated end boundary.
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
                None,
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
    visit_minutes=DEFAULT_SUGGESTED_VISIT_MINUTES,
):
    """
    Find a possible visit slot of `visit_minutes` length (the event-type-
    aware estimate from classify_event_duration -- see Phase 5.2).

    event_hours is (start, end), where end may be None when the event
    only lists a single start time and no official closing time (see
    parse_event_hours). When end is None, the search window is bounded
    only by the user's preferred time window and the day itself -- never
    by an invented event closing time, so an adaptive visit_minutes
    longer than some old universal default can still be placed.

    A 30-minute buffer is kept between suggested
    visits to avoid producing an unrealistic schedule.
    """

    event_start, event_end = event_hours

    start = max(
        event_start,
        window[0],
    )

    stop = min(
        window[1],
        24 * 60,
    )

    if event_end is not None:
        stop = min(
            stop,
            event_end,
        )

    for proposed in range(
        start,
        stop - visit_minutes + 1,
        15,
    ):
        end = (
            proposed
            + visit_minutes
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
        int -> when the user types a specific number
        None -> when the user chooses "All" (also accepts the
                legacy "Any" value, since that is still what some
                older bookmarked links / form submissions send)

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
        in ("any", "all", "")
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
                "Please type a valid number of events per day, "
                "or choose All."
            ) from None

        if not (
            1
            <= count
            <= MAX_EVENTS_PER_DAY
        ):
            raise ValueError(
                "Please choose a number between 1 and "
                f"{MAX_EVENTS_PER_DAY}, or choose All."
            )

    # -----------------------------
    # At least one date is required
    # -----------------------------
    # The date-row inputs are no longer HTML-`required` (the range picker
    # can supply every date on its own, so an extra unused "+ Add another
    # day" row is allowed to stay blank) -- so a blank entry here is a
    # normal, expected thing to filter out, not a validation error.
    raw_dates = [
        raw
        for raw in raw_dates
        if raw and raw.strip()
    ]

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
    exclude_ids=None,
):
    """
    Generate personalized Smart Schedule suggestions.

    exclude_ids:
        event ids to try LAST (used by "Regenerate": the user didn't
        like the previous suggestions, so other events are offered
        first; the old ones only fill in if nothing else fits).

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

    if exclude_ids:
        avoid = set(exclude_ids)
        eligible = (
            [event for event in eligible if event.id not in avoid]
            + [event for event in eligible if event.id in avoid]
        )

    # Phase 1.1/1.4: "why was this event recommended" -- grounded in
    # the same real signals (ratings/favorites/calendar/explicit
    # preferences/search) already used for the homepage, not a
    # separate guess. Computed once for the whole eligible pool.
    recommendation_reasons = explain_recommendations(
        user_id,
        eligible,
    )

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

            (
                duration_bucket,
                visit_minutes,
                type_label,
            ) = classify_event_duration(event)

            hours = parse_event_hours(
                event.time
            )

            if hours is None:
                # Phase 1.3: do not invent hours when the event's
                # actual time information is unclear -- but surface it
                # as a clearly-tentative suggestion instead of silently
                # dropping a personally relevant event. The user must
                # confirm the exact time themselves before attending.
                items.append(
                    {
                        "event": event,
                        "event_id": event.id,
                        "date": day,
                        "suggested_start": None,
                        "suggested_end": None,
                        "listed_hours": event.time,
                        "visit_minutes": visit_minutes,
                        "duration_bucket": duration_bucket,
                        "needs_time_confirmation": True,
                        "start_time_is_estimated": None,
                        "end_time_is_estimated": None,
                        "time_label": None,
                        "recommendation_reason": recommendation_reasons.get(
                            event.id
                        ),
                        "explanation": describe_suggested_duration(
                            duration_bucket,
                            visit_minutes,
                            type_label,
                        ),
                        "timing_warning": (
                            "No reliable listed time was found for this "
                            "event. Confirm the exact time on the "
                            "official event link before attending."
                        ),
                    }
                )

                used_ids.add(
                    event.id
                )

                continue

            event_start, event_end = hours

            if duration_bucket == "fixed":
                # ---------------------------------------------------
                # Phase 1.3: a fixed-session event (football match,
                # theatre, cinema, concert, workshop...) is never
                # shifted to another start time. Its official start
                # is event_start, full stop -- it is only ever offered
                # if that official start actually falls inside the
                # user's chosen preferred-time window, never moved
                # into it.
                # ---------------------------------------------------
                slot_start = event_start

                # The start is always official for a fixed-bucket event
                # (it is never shifted -- see the window check below).
                start_is_estimated = False

                if event_end is not None:
                    # A genuine two-time listed range -- the end is
                    # ALSO the verified official session end. Fully
                    # official case.
                    slot_end = event_end
                    end_is_estimated = False
                else:
                    # Only a start time was listed. MIXED case: the
                    # start is official, but the end is AFAQ's own
                    # duration estimate for this event type.
                    slot_end = event_start + visit_minutes
                    end_is_estimated = True

                window_start, window_end = TIME_WINDOWS[preferred_time]

                fits_preferred_window = (
                    window_start
                    <= slot_start
                    < window_end
                )

                no_conflict = all(
                    (
                        slot_end + BUFFER_MINUTES
                        <= taken_start
                    )
                    or (
                        slot_start
                        >= taken_end + BUFFER_MINUTES
                    )
                    for (
                        taken_start,
                        taken_end,
                    ) in busy
                )

                if not fits_preferred_window or not no_conflict:
                    # A fixed session cannot be moved to fit -- if it
                    # doesn't fit this window/day, it is simply not
                    # offered for it (never shifted).
                    continue

                start, end = slot_start, slot_end

            else:
                # Flexible event: AFAQ itself selects a suitable
                # sub-window inside the listed operating hours. This
                # chosen attendance time is always an estimate, never
                # an official session time.
                slot = _find_visit_slot(
                    hours,
                    TIME_WINDOWS[
                        preferred_time
                    ],
                    busy,
                    visit_minutes,
                )

                if slot is None:
                    continue

                start, end = slot
                start_is_estimated = True
                end_is_estimated = True

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
                    "visit_minutes": visit_minutes,
                    "duration_bucket": duration_bucket,
                    "needs_time_confirmation": False,
                    "start_time_is_estimated": start_is_estimated,
                    "end_time_is_estimated": end_is_estimated,
                    "time_label": describe_time_basis(
                        start_is_estimated,
                        end_is_estimated,
                    ),
                    "recommendation_reason": recommendation_reasons.get(
                        event.id
                    ),
                    "explanation": describe_suggested_duration(
                        duration_bucket,
                        visit_minutes,
                        type_label,
                        # The duration itself is an estimate whenever
                        # the END boundary is an estimate (fully
                        # official and mixed-with-official-end cases
                        # are the only ones where it isn't).
                        duration_is_estimated=end_is_estimated,
                    ),
                    "timing_warning": None,
                }
            )

            busy.append(
                (start, end)
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


def summarize_schedule_strategy(schedule, preferred_time, max_per_day):
    """
    Phase 1.8 (additive, display-only): makes it visible that the Smart
    Schedule is actually BUILT from the user's real signals -- their
    favorited/calendared/highly-rated categories and explicit
    preferences, via the same explain_recommendations() reasons already
    attached to each item -- rather than a generic "here are some
    events" list. This never changes which events were picked or their
    order; it only narrates the picks that are already there.

    Returns (overall_summary, schedule) where `schedule` is the same
    list of day-dicts passed in, each with one new "day_summary" key
    added (mutated in place, since both the Jinja template and
    store_proposed_schedule() only ever read existing keys off these
    dicts, never enumerate all of them -- an extra key is safe for both).
    """
    time_labels = {
        "morning": "mornings",
        "afternoon": "afternoons",
        "evening": "evenings",
        "any": "any time of day",
    }
    time_label = time_labels.get(preferred_time, "any time of day")

    cap_label = (
        "no daily limit"
        if max_per_day is None
        else f"up to {max_per_day} event{'s' if max_per_day != 1 else ''}/day"
    )

    total_items = sum(len(day["items"]) for day in schedule)
    active_days = sum(1 for day in schedule if day["items"])

    # Pull the real signal out of each item's already-computed
    # recommendation_reason -- "Recommended because ..." reasons are
    # personalized (favorites/ratings/preferences/search); the
    # "Popular in X" fallback is not. Counting which categories had a
    # REAL personalized match (not just a popularity fallback) is what
    # lets the summary honestly say "matched to your X interest"
    # instead of just listing categories that happened to be picked.
    personalized_categories = Counter()
    all_categories = Counter()
    for day in schedule:
        for item in day["items"]:
            category = item["event"].category
            if not category:
                continue
            all_categories[category] += 1
            reason = item.get("recommendation_reason") or ""
            if reason.startswith("Recommended because"):
                personalized_categories[category] += 1

    top_categories = [c for c, _ in (personalized_categories or all_categories).most_common(2)]

    if not total_items:
        overall_summary = (
            f"No events matched {active_days and 'these' or 'the selected'} dates with your "
            f"{time_label} preference and {cap_label} -- try widening either one."
        )
    elif personalized_categories:
        overall_summary = (
            f"Built {total_items} suggestion{'s' if total_items != 1 else ''} across "
            f"{active_days} day{'s' if active_days != 1 else ''}, matched to your interest in "
            f"{' and '.join(top_categories)}, within {time_label} and {cap_label}."
        )
    else:
        overall_summary = (
            f"Built {total_items} suggestion{'s' if total_items != 1 else ''} across "
            f"{active_days} day{'s' if active_days != 1 else ''}, within {time_label} and "
            f"{cap_label}. AFAQ doesn't have enough history on you yet for a personalized "
            f"match, so these are currently ranked by overall popularity."
        )

    for day in schedule:
        if not day["items"]:
            existing_count = day.get("existing_count") or 0
            day["day_summary"] = (
                f"Already has {existing_count} event{'s' if existing_count != 1 else ''} "
                f"saved for this day -- no further matches right now."
                if existing_count
                else None
            )
            continue

        day_categories = Counter(
            item["event"].category for item in day["items"] if item["event"].category
        )
        top_day_category = day_categories.most_common(1)[0][0] if day_categories else None
        count = len(day["items"])

        if top_day_category and len(day_categories) == 1:
            day["day_summary"] = (
                f"{count} {top_day_category.lower()} pick{'s' if count != 1 else ''} for this day."
            )
        elif top_day_category:
            day["day_summary"] = (
                f"{count} picks for this day, led by {top_day_category}."
            )
        else:
            day["day_summary"] = f"{count} pick{'s' if count != 1 else ''} for this day."

    return overall_summary, schedule


def minutes_from_time(value):
    """Convert a datetime.time (or None) to minutes since midnight."""
    if value is None:
        return None
    return value.hour * 60 + value.minute


def find_calendar_time_conflict(
    user_id,
    scheduled_date,
    start_minutes,
    end_minutes,
    exclude_calendar_entry_id=None,
):
    """
    Phase 1.7: check a proposed attendance time against the user's
    OTHER Personal Calendar entries already saved for that same date.

    Only entries that already have a saved attendance time are
    considered -- a date-only entry (no time saved) has nothing to
    conflict with here, matching the grouping AFAQ already uses.

    Returns the conflicting CalendarEvent, or None if there is no
    conflict (respecting the same BUFFER_MINUTES transition buffer
    Smart Schedule itself uses).
    """
    query = CalendarEvent.query.filter(
        CalendarEvent.user_id == user_id,
        CalendarEvent.scheduled_date == scheduled_date,
    )

    if exclude_calendar_entry_id is not None:
        query = query.filter(
            CalendarEvent.id != exclude_calendar_entry_id
        )

    for other in query.all():
        other_start = minutes_from_time(
            getattr(other, "attendance_start_time", None)
        )
        other_end = minutes_from_time(
            getattr(other, "attendance_end_time", None)
        )

        if other_start is None or other_end is None:
            continue

        no_conflict = (
            end_minutes + BUFFER_MINUTES <= other_start
        ) or (
            start_minutes >= other_end + BUFFER_MINUTES
        )

        if not no_conflict:
            return other

    return None
