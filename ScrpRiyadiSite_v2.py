import json
import requests
import os
import re
import html

from datetime import datetime, date


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

API_URL = "https://api.riyadh.sa/api/Events"

HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "en",
}

SAVE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

TIMEOUT = 20


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------

def clean_text(value):
    """
    Clean HTML and HTML entities.

    Example:
        Fadel Chaker&#039;s concert
    becomes:
        Fadel Chaker's concert
    """

    if value is None:
        return ""

    text = str(value)

    # Convert HTML entities:
    # &quot; -> "
    # &#039; -> '
    # &amp; -> &
    text = html.unescape(text)

    # Remove HTML tags.
    text = re.sub(
        r"<[^>]+>",
        "",
        text
    )

    # Remove non-breaking spaces.
    text = text.replace(
        "\xa0",
        " "
    )

    # Normalize whitespace while preserving readable paragraphs.
    text = re.sub(
        r"[ \t]+",
        " ",
        text
    )

    text = re.sub(
        r"\n\s*\n+",
        "\n\n",
        text
    )

    return text.strip()


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def parse_api_date(value):
    """
    Convert Riyadh.sa date into datetime.date.
    """

    if value is None or value == "":
        return None

    try:
        timestamp = int(value)

        # In case milliseconds are ever returned.
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000

        return datetime.fromtimestamp(
            timestamp
        ).date()

    except (
        ValueError,
        TypeError,
        OSError,
        OverflowError,
    ):
        pass

    text = str(value).strip()

    try:
        return datetime.strptime(
            text,
            "%Y-%m-%d"
        ).date()

    except ValueError:
        return None


def format_date(value):
    """
    Return YYYY-MM-DD string.
    """

    parsed = parse_api_date(
        value
    )

    if not parsed:
        return ""

    return parsed.strftime(
        "%Y-%m-%d"
    )


# ---------------------------------------------------------------------------
# Time helper
# ---------------------------------------------------------------------------

def format_time(time_value):
    """
    Convert API time structure into:

        HH:MM - HH:MM

    If start and finish are identical, the API time is treated
    as unspecified and an empty value is returned.
    """

    if (
        not time_value
        or not isinstance(
            time_value,
            dict
        )
    ):
        return ""

    try:

        start_seconds = int(
            time_value.get(
                "start_time",
                0
            )
            or 0
        )

        finish_seconds = int(
            time_value.get(
                "finish_time",
                0
            )
            or 0
        )

        # Riyadh.sa sometimes returns the same start and finish
        # time as a placeholder. Do not display it as a real time.
        if start_seconds == finish_seconds:
            return ""

        def seconds_to_time(seconds):

            hours = (
                seconds // 3600
            )

            minutes = (
                seconds % 3600
            ) // 60

            return (
                f"{hours:02d}:"
                f"{minutes:02d}"
            )

        start_time = (
            seconds_to_time(
                start_seconds
            )
        )

        finish_time = (
            seconds_to_time(
                finish_seconds
            )
        )

        return (
            f"{start_time} - "
            f"{finish_time}"
        )

    except (
        ValueError,
        TypeError,
    ):
        return ""# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------

def extract_category(event):
    """
    Extract English category name.
    """

    category = event.get(
        "category",
        ""
    )

    if (
        isinstance(
            category,
            list
        )
        and category
    ):

        first = category[0]

        if isinstance(
            first,
            dict
        ):
            return clean_text(
                first.get(
                    "taxonomy_term_name",
                    ""
                )
            )

    if isinstance(
        category,
        dict
    ):
        return clean_text(
            category.get(
                "taxonomy_term_name",
                ""
            )
        )

    return clean_text(
        category
    )


# ---------------------------------------------------------------------------
# Image
# ---------------------------------------------------------------------------

def extract_image(event):
    """
    Extract first event image.
    """

    image = event.get(
        "image",
        ""
    )

    if (
        isinstance(
            image,
            list
        )
        and image
    ):

        first = image[0]

        if isinstance(
            first,
            dict
        ):
            return (
                first.get(
                    "url",
                    ""
                )
                or ""
            )

    if isinstance(
        image,
        str
    ):
        return image.strip()

    return ""


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

def extract_location(event):
    """
    Store coordinates as:

        latitude,longitude

    This format works directly with Google Maps search.

    Example:
        24.753104900769,46.726874223947
    """

    geofield = event.get(
        "geofield"
    )

    if not isinstance(
        geofield,
        dict
    ):
        return ""

    lat = geofield.get(
        "lat"
    )

    lon = geofield.get(
        "lon"
    )

    if (
        lat is None
        or lon is None
    ):
        return ""

    return (
        f"{lat},{lon}"
    )


def extract_address(event):
    """
    Extract readable venue/address for inspection.

    We keep it in the JSON for now even though the current Event
    database model does not yet use a separate address column.
    """

    geofield = event.get(
        "geofield"
    )

    if not isinstance(
        geofield,
        dict
    ):
        return ""

    return clean_text(
        geofield.get(
            "address",
            ""
        )
    )


# ---------------------------------------------------------------------------
# Fetch API
# ---------------------------------------------------------------------------

def fetch_all_events():
    """
    Fetch all available Riyadh.sa events.
    """

    all_events = []
    page_number = 0

    print(
        "Fetching events from Riyadh.sa..."
    )

    while True:

        params = {
            "_format": "json",
            "langcode": "en",
            "page": page_number,
        }

        try:
            response = requests.get(
                API_URL,
                params=params,
                headers=HEADERS,
                timeout=TIMEOUT,
            )

            response.raise_for_status()

        except requests.RequestException as error:

            print(
                f"ERROR on page "
                f"{page_number}: "
                f"{error}"
            )

            break

        try:
            payload = response.json()

        except ValueError:

            print(
                f"ERROR: Page "
                f"{page_number} "
                f"did not return JSON."
            )

            break

        items = (
            payload
            .get(
                "result",
                {}
            )
            .get(
                "items",
                []
            )
        )

        if not items:

            print(
                f"No more events after "
                f"page {page_number - 1}."
            )

            break

        print(
            f"Page {page_number}: "
            f"{len(items)} events"
        )

        all_events.extend(
            items
        )

        page_number += 1

    return all_events


# ---------------------------------------------------------------------------
# Parse one event
# ---------------------------------------------------------------------------

def parse_event(event):
    """
    Convert raw Riyadh API event into AFAQ format.
    """

    return {
        "title": clean_text(
            event.get(
                "title",
                ""
            )
        ),

        "category": extract_category(
            event
        ),

        "city": "Riyadh",

        "date": format_date(
            event.get(
                "start_date"
            )
        ),

        "time": format_time(
            event.get(
                "time"
            )
        ),

        "description": clean_text(
            event.get(
                "body",
                ""
            )
        ),

        "image": extract_image(
            event
        ),

        "official_link": (
            event.get(
                "link",
                ""
            )
            or ""
        ).strip(),

        "source": "Riyadh.sa",

        "price": clean_text(
            event.get(
                "cost",
                ""
            )
        ),

        # Used by AFAQ's Google Maps link.
        "location": extract_location(
            event
        ),

        # Kept in JSON for inspection.
        "address": extract_address(
            event
        ),

        "end_date": format_date(
            event.get(
                "finish_date"
            )
        ),

        # We are intentionally leaving this empty in this first V2 step.
        # We will test registration-link extraction separately.
        "external_link": "",
    }


# ---------------------------------------------------------------------------
# Remove expired events
# ---------------------------------------------------------------------------

def is_current_or_future(event):
    """
    Keep events whose effective end date is today or later.
    """

    today = date.today()

    start_date = parse_api_date(
        event.get(
            "start_date"
        )
    )

    end_date = parse_api_date(
        event.get(
            "finish_date"
        )
    )

    effective_end = (
        end_date
        or start_date
    )

    if not effective_end:
        return False

    return (
        effective_end
        >= today
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():

    print(
        "\n=== AFAQ Riyadh Events V2 ===\n"
    )

    raw_events = (
        fetch_all_events()
    )

    print(
        f"\nTotal received from API: "
        f"{len(raw_events)}"
    )

    current_events = [
        event
        for event in raw_events
        if is_current_or_future(
            event
        )
    ]

    expired_count = (
        len(raw_events)
        - len(current_events)
    )

    print(
        f"Current/Future: "
        f"{len(current_events)}"
    )

    print(
        f"Expired skipped: "
        f"{expired_count}"
    )

    parsed_events = [
        parse_event(
            event
        )
        for event in current_events
    ]

    # Remove records without a usable title/date.
    valid_events = [
        event
        for event in parsed_events
        if (
            event["title"]
            and event["date"]
        )
    ]

    invalid_count = (
        len(parsed_events)
        - len(valid_events)
    )

    # -------------------------------------------------------
    # Save to NEW file.
    # Does not overwrite old data.
    # -------------------------------------------------------

    json_path = os.path.join(
        SAVE_DIR,
        "riyadh_events_v2.json"
    )

    with open(
        json_path,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            valid_events,
            file,
            ensure_ascii=False,
            indent=2
        )

    # -------------------------------------------------------
    # Summary
    # -------------------------------------------------------

    with_location = sum(
        1
        for event in valid_events
        if event["location"]
    )

    with_address = sum(
        1
        for event in valid_events
        if event["address"]
    )

    with_image = sum(
        1
        for event in valid_events
        if event["image"]
    )

    print(
        "\n=============================="
    )

    print(
        "AFAQ V2 EXPORT COMPLETE"
    )

    print(
        "=============================="
    )

    print(
        f"Saved events:      "
        f"{len(valid_events)}"
    )

    print(
        f"Invalid skipped:   "
        f"{invalid_count}"
    )

    print(
        f"With coordinates:  "
        f"{with_location}"
    )

    print(
        f"With address:      "
        f"{with_address}"
    )

    print(
        f"With image:        "
        f"{with_image}"
    )

    print(
        f"\nJSON saved to:\n"
        f"{json_path}"
    )

    print(
        "\nNO DATABASE CHANGES WERE MADE."
    )


if __name__ == "__main__":
    main()