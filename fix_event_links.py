import json
import os
import html

import requests

from datetime import datetime, date
from html.parser import HTMLParser
from urllib.parse import urlparse

from sqlalchemy import create_engine, text

from config import Config


# ============================================================
# SETTINGS
# ============================================================

API_URL = "https://api.riyadh.sa/api/Events"

HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "en",
}

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

BACKUP_DIR = os.path.join(
    BASE_DIR,
    "backups"
)


# ============================================================
# HTML PARSER
# ============================================================

class AnchorParser(HTMLParser):

    def __init__(self):
        super().__init__()

        self.links = []
        self.current_href = None
        self.current_text = []

    def handle_starttag(self, tag, attrs):

        if tag.lower() != "a":
            return

        attrs = dict(attrs)

        self.current_href = attrs.get(
            "href",
            ""
        )

        self.current_text = []

    def handle_data(self, data):

        if self.current_href is not None:

            self.current_text.append(
                data
            )

    def handle_endtag(self, tag):

        if (
            tag.lower() == "a"
            and self.current_href is not None
        ):

            text_value = " ".join(
                "".join(
                    self.current_text
                ).split()
            )

            self.links.append({
                "text": html.unescape(
                    text_value
                ).strip(),

                "href": html.unescape(
                    self.current_href
                ).strip(),
            })

            self.current_href = None
            self.current_text = []


# ============================================================
# GENERAL HELPERS
# ============================================================

def normalize_text(value):

    if value is None:
        return ""

    # Converts:
    # &amp;   -> &
    # &quot;  -> "
    # &#039;  -> '
    value = html.unescape(
        str(value)
    )

    return " ".join(
        value.strip().split()
    )


def normalize_title(value):

    return normalize_text(
        value
    ).casefold()


def parse_date(value):

    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    try:

        number = int(value)

        # Milliseconds
        if number > 10_000_000_000:
            number = number / 1000

        return datetime.fromtimestamp(
            number
        ).date()

    except (
        ValueError,
        TypeError,
        OSError,
        OverflowError,
    ):
        pass

    text_value = str(
        value
    ).strip()

    try:

        return datetime.strptime(
            text_value,
            "%Y-%m-%d"
        ).date()

    except ValueError:

        return None


def get_domain(url):

    if not url:
        return ""

    try:

        return (
            urlparse(url)
            .netloc
            .lower()
            .replace(
                "www.",
                ""
            )
        )

    except Exception:

        return ""


def is_riyadh_link(url):

    domain = get_domain(
        url
    )

    return (
        domain == "riyadh.sa"
        or
        domain.endswith(
            ".riyadh.sa"
        )
    )


def is_external_link(url):

    if not url:
        return False

    url = url.strip()

    if not (
        url.startswith(
            "http://"
        )
        or
        url.startswith(
            "https://"
        )
    ):
        return False

    domain = get_domain(
        url
    )

    if not domain:
        return False

    if is_riyadh_link(
        url
    ):
        return False

    return True


# ============================================================
# EXTRACT LINKS FROM EVENT BODY
# ============================================================

def extract_links(body):

    if not body:
        return []

    parser = AnchorParser()

    try:

        parser.feed(
            str(body)
        )

    except Exception:

        return []

    return parser.links


def link_score(link):

    button_text = normalize_text(
        link.get(
            "text",
            ""
        )
    ).lower()

    href = normalize_text(
        link.get(
            "href",
            ""
        )
    ).lower()

    score = 0

    # Button text priorities
    text_keywords = {
        "book now": 100,
        "book tickets": 100,
        "buy now": 100,
        "buy tickets": 100,
        "register now": 95,
        "registration": 90,
        "register": 90,
        "apply now": 85,
        "tickets": 80,
        "ticket": 80,
        "booking": 80,
        "book": 75,
        "know more": 40,
        "learn more": 40,
        "live stream": 30,
    }

    for keyword, points in (
        text_keywords.items()
    ):

        if keyword in button_text:

            score = max(
                score,
                points
            )

    # URL priorities
    url_keywords = {
        "webook": 90,
        "platinumlist": 90,
        "ticket": 80,
        "book": 80,
        "register": 80,
        "registration": 80,
    }

    for keyword, points in (
        url_keywords.items()
    ):

        if keyword in href:

            score = max(
                score,
                points
            )

    return score


def choose_best_link(body):

    links = extract_links(
        body
    )

    candidates = [
        link
        for link in links
        if is_external_link(
            link.get(
                "href",
                ""
            )
        )
    ]

    if not candidates:
        return None

    candidates = sorted(
        candidates,
        key=link_score,
        reverse=True,
    )

    best = candidates[0]

    # Do not select random social/media links.
    if link_score(
        best
    ) <= 0:

        return None

    return best


# ============================================================
# RIYADH API
# ============================================================

def fetch_events():

    events = []
    page_number = 0

    print()
    print(
        "Fetching Riyadh.sa events..."
    )

    while True:

        response = requests.get(
            API_URL,
            params={
                "_format": "json",
                "langcode": "en",
                "page": page_number,
            },
            headers=HEADERS,
            timeout=20,
        )

        response.raise_for_status()

        data = response.json()

        items = (
            data
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
            break

        print(
            f"Page {page_number}: "
            f"{len(items)} events"
        )

        events.extend(
            items
        )

        page_number += 1

    print()
    print(
        f"Total API events: "
        f"{len(events)}"
    )

    return events


# ============================================================
# DATABASE
# ============================================================

def create_db_engine():

    return create_engine(
        Config.SQLALCHEMY_DATABASE_URI,
        pool_pre_ping=True,
    )


def load_database_events(engine):

    query = text(
        """
        SELECT
            id,
            title,
            start_date,
            official_link,
            is_active
        FROM events
        """
    )

    with engine.connect() as connection:

        rows = connection.execute(
            query
        )

        return [
            dict(
                row._mapping
            )
            for row in rows
        ]


# ============================================================
# BUILD SAFE UPDATE PLAN
# ============================================================

def build_plan(
    api_events,
    database_events,
):

    db_index = {}

    # Index database by:
    # normalized title + start date
    for event in database_events:

        key = (
            normalize_title(
                event.get(
                    "title"
                )
            ),
            parse_date(
                event.get(
                    "start_date"
                )
            ),
        )

        if (
            key[0]
            and key[1]
        ):

            db_index.setdefault(
                key,
                []
            ).append(
                event
            )

    updates = []
    already_good = []
    preserved = []
    no_direct_link = []
    inactive_skipped = []
    no_match = []
    conflicts = []

    for api_event in api_events:

        title = normalize_text(
            api_event.get(
                "title"
            )
        )

        start_date = parse_date(
            api_event.get(
                "start_date"
            )
        )

        key = (
            normalize_title(
                title
            ),
            start_date,
        )

        matches = db_index.get(
            key,
            []
        )

        # No matching event in DB
        if not matches:

            no_match.append({
                "title": title,
                "date": (
                    str(start_date)
                    if start_date
                    else ""
                ),
            })

            continue

        # More than one DB match = unsafe
        if len(matches) > 1:

            conflicts.append({
                "title": title,
                "date": (
                    str(start_date)
                    if start_date
                    else ""
                ),
                "matches": len(matches),
            })

            continue

        db_event = matches[0]

        # Do not touch inactive events
        if not bool(
            db_event.get(
                "is_active"
            )
        ):

            inactive_skipped.append({
                "id": db_event.get(
                    "id"
                ),
                "title": title,
            })

            continue

        best_link = choose_best_link(
            api_event.get(
                "body",
                ""
            )
        )

        # API event has no usable
        # external booking/register link
        if not best_link:

            no_direct_link.append({
                "id": db_event.get(
                    "id"
                ),
                "title": title,
                "current_link": (
                    db_event.get(
                        "official_link"
                    )
                    or ""
                ),
            })

            continue

        direct_url = normalize_text(
            best_link.get(
                "href"
            )
        )

        button_text = normalize_text(
            best_link.get(
                "text"
            )
        )

        current_url = normalize_text(
            db_event.get(
                "official_link"
            )
        )

        # Already has this exact direct URL
        if current_url == direct_url:

            already_good.append({
                "id": db_event.get(
                    "id"
                ),
                "title": title,
            })

            continue

        # Existing external official link:
        # preserve it.
        if (
            current_url
            and
            not is_riyadh_link(
                current_url
            )
        ):

            preserved.append({
                "id": db_event.get(
                    "id"
                ),
                "title": title,
                "current_link": current_url,
                "found_link": direct_url,
            })

            continue

        # If current link is empty
        # OR Riyadh.sa:
        # replace it with the real
        # booking/register URL.
        updates.append({
            "id": db_event.get(
                "id"
            ),
            "title": title,
            "old_link": current_url,
            "new_link": direct_url,
            "button_text": button_text,
        })

    return {
        "updates": updates,
        "already_good": already_good,
        "preserved": preserved,
        "no_direct_link": no_direct_link,
        "inactive_skipped": inactive_skipped,
        "no_match": no_match,
        "conflicts": conflicts,
    }


# ============================================================
# BACKUP
# ============================================================

def create_backup(plan):

    os.makedirs(
        BACKUP_DIR,
        exist_ok=True
    )

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    backup_path = os.path.join(
        BACKUP_DIR,
        (
            "event_links_before_fix_"
            f"{timestamp}.json"
        )
    )

    with open(
        backup_path,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            plan["updates"],
            file,
            ensure_ascii=False,
            indent=2
        )

    return backup_path


# ============================================================
# APPLY CHANGES
# ============================================================

def apply_updates(
    engine,
    plan,
):

    query = text(
        """
        UPDATE events
        SET official_link = :new_link
        WHERE id = :event_id
          AND is_active = 1
        """
    )

    with engine.begin() as connection:

        for item in plan[
            "updates"
        ]:

            connection.execute(
                query,
                {
                    "new_link": (
                        item[
                            "new_link"
                        ]
                    ),
                    "event_id": (
                        item[
                            "id"
                        ]
                    ),
                }
            )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print(
        "=" * 72
    )

    print(
        "AFAQ EVENT LINK FIX"
    )

    print(
        "=" * 72
    )

    engine = create_db_engine()

    try:

        api_events = fetch_events()

        database_events = (
            load_database_events(
                engine
            )
        )

        print(
            f"Total DB events: "
            f"{len(database_events)}"
        )

        plan = build_plan(
            api_events,
            database_events,
        )

        # ====================================================
        # SUMMARY
        # ====================================================

        print()
        print(
            "=" * 72
        )

        print(
            "LINK FIX PLAN"
        )

        print(
            "=" * 72
        )

        print(
            f"Will UPDATE:          "
            f"{len(plan['updates'])}"
        )

        print(
            f"Already correct:      "
            f"{len(plan['already_good'])}"
        )

        print(
            f"Existing links kept:  "
            f"{len(plan['preserved'])}"
        )

        print(
            f"No direct link:        "
            f"{len(plan['no_direct_link'])}"
        )

        print(
            f"Inactive skipped:      "
            f"{len(plan['inactive_skipped'])}"
        )

        print(
            f"No DB match:           "
            f"{len(plan['no_match'])}"
        )

        print(
            f"Conflicts:             "
            f"{len(plan['conflicts'])}"
        )

        # ====================================================
        # LINKS THAT WILL CHANGE
        # ====================================================

        if plan[
            "updates"
        ]:

            print()
            print(
                "=" * 72
            )

            print(
                "LINKS THAT WILL CHANGE"
            )

            print(
                "=" * 72
            )

            for item in plan[
                "updates"
            ]:

                print()
                print(
                    f"DB ID "
                    f"{item['id']} "
                    f"- "
                    f"{item['title']}"
                )

                print(
                    f"  Button: "
                    f"{item['button_text']}"
                )

                print(
                    f"  OLD: "
                    f"{item['old_link']}"
                )

                print(
                    f"  NEW: "
                    f"{item['new_link']}"
                )

        # ====================================================
        # NO DB MATCH
        # ====================================================

        if plan[
            "no_match"
        ]:

            print()
            print(
                "=" * 72
            )

            print(
                "NO DB MATCH"
            )

            print(
                "=" * 72
            )

            for item in plan[
                "no_match"
            ]:

                print(
                    f"- "
                    f"{item['title']} "
                    f"({item['date']})"
                )

        # ====================================================
        # CONFLICTS
        # ====================================================

        if plan[
            "conflicts"
        ]:

            print()
            print(
                "=" * 72
            )

            print(
                "CONFLICTS"
            )

            print(
                "=" * 72
            )

            for item in plan[
                "conflicts"
            ]:

                print(
                    f"- "
                    f"{item['title']} "
                    f"({item['date']}) "
                    f"- "
                    f"{item['matches']} "
                    f"DB matches"
                )

            print()
            print(
                "STOPPED: "
                "conflicts were found."
            )

            print(
                "NO DATABASE CHANGES "
                "WERE MADE."
            )

            return

        # ====================================================
        # BACKUP
        # ====================================================

        backup_path = (
            create_backup(
                plan
            )
        )

        print()
        print(
            "=" * 72
        )

        print(
            "Backup created:"
        )

        print(
            backup_path
        )

        print()
        print(
            "NO DATABASE CHANGES "
            "HAVE BEEN MADE YET."
        )

        print()

        # ====================================================
        # CONFIRM
        # ====================================================

        confirmation = input(
            "Type FIXLINKS exactly "
            "to apply the changes: "
        )

        if (
            confirmation.strip()
            != "FIXLINKS"
        ):

            print()
            print(
                "Cancelled."
            )

            print(
                "NO DATABASE CHANGES "
                "WERE MADE."
            )

            return

        # ====================================================
        # APPLY
        # ====================================================

        print()
        print(
            "Applying link changes..."
        )

        apply_updates(
            engine,
            plan,
        )

        # ====================================================
        # FINISHED
        # ====================================================

        print()
        print(
            "=" * 72
        )

        print(
            "LINK FIX COMPLETE"
        )

        print(
            "=" * 72
        )

        print(
            f"Updated links: "
            f"{len(plan['updates'])}"
        )

        print(
            f"Already correct: "
            f"{len(plan['already_good'])}"
        )

        print(
            f"Existing external "
            f"links preserved: "
            f"{len(plan['preserved'])}"
        )

        print(
            f"No direct link: "
            f"{len(plan['no_direct_link'])}"
        )

        print(
            f"Inactive skipped: "
            f"{len(plan['inactive_skipped'])}"
        )

        print(
            "NO EVENTS WERE DELETED."
        )

        print(
            "INACTIVE EVENTS "
            "WERE NOT CHANGED."
        )

        print(
            "=" * 72
        )

    except Exception as error:

        print()
        print(
            "=" * 72
        )

        print(
            "ERROR"
        )

        print(
            "=" * 72
        )

        print(
            error
        )

        print()
        print(
            "The link update was not "
            "completed."
        )

    finally:

        engine.dispose()


if __name__ == "__main__":
    main()