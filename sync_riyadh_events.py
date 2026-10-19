import json
import os

from datetime import datetime, date
from sqlalchemy import create_engine, text

from config import Config


# ============================================================
# SETTINGS
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

JSON_PATH = os.path.join(
    BASE_DIR,
    "riyadh_events_v2.json"
)

BACKUP_DIR = os.path.join(
    BASE_DIR,
    "backups"
)


# ============================================================
# HELPERS
# ============================================================

def normalize_text(value):
    if value is None:
        return ""

    return " ".join(
        str(value).strip().split()
    )


def normalize_title(value):
    return normalize_text(
        value
    ).casefold()


def normalize_url(value):
    return normalize_text(
        value
    ).rstrip("/")


def parse_date(value):

    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    try:
        return datetime.strptime(
            str(value).strip(),
            "%Y-%m-%d"
        ).date()

    except ValueError:
        return None


def json_safe(value):

    if isinstance(
        value,
        (datetime, date)
    ):
        return value.isoformat()

    return value


# ============================================================
# LOAD JSON
# ============================================================

def load_new_events():

    if not os.path.exists(
        JSON_PATH
    ):
        raise FileNotFoundError(
            f"Could not find:\n{JSON_PATH}"
        )

    with open(
        JSON_PATH,
        "r",
        encoding="utf-8"
    ) as file:

        data = json.load(file)

    if not isinstance(
        data,
        list
    ):
        raise ValueError(
            "riyadh_events_v2.json "
            "must contain a list."
        )

    return data


# ============================================================
# FORMAT NEW EVENT
# ============================================================

def format_new_event(event):

    return {
        "title": normalize_text(
            event.get("title")
        ),

        "category": normalize_text(
            event.get("category")
        ),

        "city": normalize_text(
            event.get("city")
        ) or "Riyadh",

        "start_date": parse_date(
            event.get("date")
        ),

        "time": normalize_text(
            event.get("time")
        ),

        "description": normalize_text(
            event.get("description")
        ),

        "image": normalize_text(
            event.get("image")
        ),

        "official_link": normalize_url(
            event.get("official_link")
        ),

        "source": normalize_text(
            event.get("source")
        ) or "Riyadh.sa",

        "price": normalize_text(
            event.get("price")
        ),

        "location": normalize_text(
            event.get("location")
        ),

        "end_date": parse_date(
            event.get("end_date")
        ),
    }


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
            category,
            city,
            start_date,
            time,
            description,
            image,
            official_link,
            source,
            price,
            location,
            end_date,
            is_active
        FROM events
        """
    )

    with engine.connect() as connection:

        result = connection.execute(
            query
        )

        return [
            dict(row._mapping)
            for row in result
        ]


# ============================================================
# BACKUP
# ============================================================

def create_backup(database_events):

    os.makedirs(
        BACKUP_DIR,
        exist_ok=True
    )

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    backup_path = os.path.join(
        BACKUP_DIR,
        f"events_before_riyadh_sync_{timestamp}.json"
    )

    safe_rows = []

    for row in database_events:

        safe_row = {}

        for key, value in row.items():

            safe_row[key] = json_safe(
                value
            )

        safe_rows.append(
            safe_row
        )

    with open(
        backup_path,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            safe_rows,
            file,
            ensure_ascii=False,
            indent=2
        )

    return backup_path


# ============================================================
# BUILD INDEXES
# ============================================================

def build_indexes(database_events):

    by_title_date = {}
    by_link_date = {}
    by_link = {}

    duplicate_title_dates = set()
    duplicate_link_dates = set()

    for event in database_events:

        title_key = (
            normalize_title(
                event.get("title")
            ),
            parse_date(
                event.get("start_date")
            ),
        )

        if (
            title_key[0]
            and title_key[1]
        ):

            if title_key in by_title_date:

                duplicate_title_dates.add(
                    title_key
                )

            else:

                by_title_date[
                    title_key
                ] = event

        link = normalize_url(
            event.get(
                "official_link"
            )
        )

        start_date = parse_date(
            event.get(
                "start_date"
            )
        )

        if link and start_date:

            link_date_key = (
                link,
                start_date,
            )

            if link_date_key in by_link_date:

                duplicate_link_dates.add(
                    link_date_key
                )

            else:

                by_link_date[
                    link_date_key
                ] = event

            by_link.setdefault(
                link,
                []
            ).append(
                event
            )

    return {
        "by_title_date": (
            by_title_date
        ),

        "by_link_date": (
            by_link_date
        ),

        "by_link": by_link,

        "duplicate_title_dates": (
            duplicate_title_dates
        ),

        "duplicate_link_dates": (
            duplicate_link_dates
        ),
    }


# ============================================================
# PLAN SYNC
# ============================================================

def build_sync_plan(
    new_events,
    database_events,
):

    indexes = build_indexes(
        database_events
    )

    inserts = []
    updates = []
    skipped_inactive = []
    conflicts = []

    seen_new_title_dates = set()

    for raw_event in new_events:

        event = format_new_event(
            raw_event
        )

        title = event["title"]
        start_date = event[
            "start_date"
        ]

        if not title:

            conflicts.append({
                "title": "(Missing title)",
                "reason": "Missing title.",
            })

            continue

        if not start_date:

            conflicts.append({
                "title": title,
                "reason": (
                    "Missing or invalid start date."
                ),
            })

            continue

        title_date_key = (
            normalize_title(
                title
            ),
            start_date,
        )

        # ----------------------------------------------------
        # Duplicate inside NEW JSON
        # ----------------------------------------------------

        if (
            title_date_key
            in seen_new_title_dates
        ):

            conflicts.append({
                "title": title,
                "reason": (
                    "Duplicate title/date "
                    "inside new JSON."
                ),
            })

            continue

        seen_new_title_dates.add(
            title_date_key
        )

        # ----------------------------------------------------
        # Unsafe duplicate already inside DB
        # ----------------------------------------------------

        if (
            title_date_key
            in indexes[
                "duplicate_title_dates"
            ]
        ):

            conflicts.append({
                "title": title,
                "reason": (
                    "Database has multiple rows "
                    "with this title and date."
                ),
            })

            continue

        # ----------------------------------------------------
        # MATCH 1:
        # Same title + same start date
        #
        # This catches the 20 records where the
        # old loader stored an external official link.
        # ----------------------------------------------------

        existing = indexes[
            "by_title_date"
        ].get(
            title_date_key
        )

        if existing:

            if not bool(
                existing.get(
                    "is_active"
                )
            ):

                skipped_inactive.append({
                    "id": existing.get(
                        "id"
                    ),

                    "title": title,

                    "reason": (
                        "Existing event is inactive. "
                        "It will NOT be reactivated."
                    ),
                })

                continue

            updates.append({
                "id": existing.get(
                    "id"
                ),
                "event": event,
                "match": (
                    "same title + start date"
                ),
            })

            continue

        # ----------------------------------------------------
        # MATCH 2:
        # Same Riyadh link + same date.
        #
        # Useful when this script is run again later.
        # ----------------------------------------------------

        official_link = event[
            "official_link"
        ]

        link_date_key = None

        if official_link:

            link_date_key = (
                official_link,
                start_date,
            )

        if (
            link_date_key
            and link_date_key
            in indexes[
                "duplicate_link_dates"
            ]
        ):

            conflicts.append({
                "title": title,
                "reason": (
                    "Multiple DB rows use the "
                    "same official link and date."
                ),
            })

            continue

        existing_by_link = None

        if link_date_key:

            existing_by_link = (
                indexes[
                    "by_link_date"
                ].get(
                    link_date_key
                )
            )

        if existing_by_link:

            if not bool(
                existing_by_link.get(
                    "is_active"
                )
            ):

                skipped_inactive.append({
                    "id": (
                        existing_by_link.get(
                            "id"
                        )
                    ),

                    "title": title,

                    "reason": (
                        "Existing event is inactive. "
                        "It will NOT be reactivated."
                    ),
                })

                continue

            updates.append({
                "id": (
                    existing_by_link.get(
                        "id"
                    )
                ),

                "event": event,

                "match": (
                    "same official link + "
                    "start date"
                ),
            })

            continue

        # ----------------------------------------------------
        # SAFETY:
        # Same official link in SAME year but
        # different start date may mean rescheduling.
        # Do not insert automatically.
        # ----------------------------------------------------

        if official_link:

            same_link_rows = (
                indexes[
                    "by_link"
                ].get(
                    official_link,
                    []
                )
            )

            same_year_rows = [
                row
                for row
                in same_link_rows
                if (
                    parse_date(
                        row.get(
                            "start_date"
                        )
                    )
                    and
                    parse_date(
                        row.get(
                            "start_date"
                        )
                    ).year
                    ==
                    start_date.year
                )
            ]

            if same_year_rows:

                conflicts.append({
                    "title": title,
                    "reason": (
                        "Same official link already "
                        "exists in the same year "
                        "with a different date. "
                        "Possible reschedule."
                    ),
                })

                continue

        # ----------------------------------------------------
        # Otherwise it is a new event/new edition.
        # ----------------------------------------------------

        inserts.append(
            event
        )

    return {
        "inserts": inserts,
        "updates": updates,
        "skipped_inactive": (
            skipped_inactive
        ),
        "conflicts": conflicts,
    }


# ============================================================
# PRINT PLAN
# ============================================================

def print_plan(plan):

    print()
    print("=" * 72)
    print("AFAQ RIYADH EVENTS SYNC PLAN")
    print("=" * 72)

    print(
        f"Will INSERT:        "
        f"{len(plan['inserts'])}"
    )

    print(
        f"Will UPDATE:        "
        f"{len(plan['updates'])}"
    )

    print(
        f"Inactive skipped:   "
        f"{len(plan['skipped_inactive'])}"
    )

    print(
        f"Conflicts:          "
        f"{len(plan['conflicts'])}"
    )

    if plan[
        "skipped_inactive"
    ]:

        print()
        print(
            "INACTIVE EVENTS THAT "
            "WILL STAY INACTIVE:"
        )

        for item in plan[
            "skipped_inactive"
        ]:

            print(
                f"- DB ID {item['id']}: "
                f"{item['title']}"
            )

    if plan[
        "conflicts"
    ]:

        print()
        print(
            "CONFLICTS:"
        )

        for item in plan[
            "conflicts"
        ]:

            print(
                f"- {item['title']}: "
                f"{item['reason']}"
            )

    print("=" * 72)


# ============================================================
# SQL
# ============================================================

UPDATE_SQL = text(
    """
    UPDATE events
    SET
        title = :title,
        category = :category,
        city = :city,

        time = CASE
            WHEN :time_value <> ''
            THEN :time_value
            ELSE time
        END,

        description = CASE
            WHEN :description <> ''
            THEN :description
            ELSE description
        END,

        image = CASE
            WHEN :image <> ''
            THEN :image
            ELSE image
        END,

        official_link = CASE
            WHEN official_link IS NULL
                 OR TRIM(official_link) = ''
            THEN NULLIF(
                :official_link,
                ''
            )
            ELSE official_link
        END,

        source = CASE
            WHEN :source <> ''
            THEN :source
            ELSE source
        END,

        price = CASE
            WHEN :price <> ''
            THEN :price
            ELSE price
        END,

        location = CASE
            WHEN :location <> ''
            THEN :location
            ELSE location
        END,

        end_date = COALESCE(
            :end_date,
            end_date
        )

    WHERE id = :id
      AND is_active = 1
    """
)


INSERT_SQL = text(
    """
    INSERT INTO events (
        title,
        category,
        city,
        start_date,
        time,
        description,
        image,
        official_link,
        source,
        price,
        location,
        end_date,
        is_active
    )
    VALUES (
        :title,
        :category,
        :city,
        :start_date,
        :time_value,
        :description,
        :image,
        NULLIF(
            :official_link,
            ''
        ),
        :source,
        NULLIF(
            :price,
            ''
        ),
        NULLIF(
            :location,
            ''
        ),
        :end_date,
        1
    )
    """
)


# ============================================================
# PARAMS
# ============================================================

def sql_params(
    event,
    event_id=None,
):

    params = {
        "title": event[
            "title"
        ],

        "category": event[
            "category"
        ],

        "city": event[
            "city"
        ],

        "start_date": event[
            "start_date"
        ],

        "time_value": event[
            "time"
        ],

        "description": event[
            "description"
        ],

        "image": event[
            "image"
        ],

        "official_link": event[
            "official_link"
        ],

        "source": event[
            "source"
        ],

        "price": event[
            "price"
        ],

        "location": event[
            "location"
        ],

        "end_date": event[
            "end_date"
        ],
    }

    if event_id is not None:

        params[
            "id"
        ] = event_id

    return params


# ============================================================
# APPLY
# ============================================================

def apply_sync(
    engine,
    plan,
):

    with engine.begin() as connection:

        # ----------------------------------------------------
        # Update existing active events.
        #
        # IMPORTANT:
        # Existing non-empty official_link is preserved.
        # IDs are preserved.
        # is_active is preserved.
        # ----------------------------------------------------

        for item in plan[
            "updates"
        ]:

            connection.execute(
                UPDATE_SQL,
                sql_params(
                    item["event"],
                    item["id"],
                )
            )

        # ----------------------------------------------------
        # Insert new events/new annual editions.
        # ----------------------------------------------------

        for event in plan[
            "inserts"
        ]:

            connection.execute(
                INSERT_SQL,
                sql_params(
                    event
                )
            )


# ============================================================
# VERIFY
# ============================================================

def verify_database(engine):

    query = text(
        """
        SELECT
            COUNT(*) AS total_events,
            SUM(
                CASE
                    WHEN is_active = 1
                    THEN 1
                    ELSE 0
                END
            ) AS active_events,
            SUM(
                CASE
                    WHEN is_active = 1
                     AND COALESCE(
                            end_date,
                            start_date
                         ) >= CURDATE()
                    THEN 1
                    ELSE 0
                END
            ) AS current_active_events
        FROM events
        """
    )

    with engine.connect() as connection:

        row = connection.execute(
            query
        ).mappings().first()

    return dict(
        row
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print(
        "AFAQ Riyadh Events Safe Sync"
    )

    print(
        "Preparing..."
    )

    engine = create_db_engine()

    try:

        new_events = (
            load_new_events()
        )

        print(
            f"New JSON events: "
            f"{len(new_events)}"
        )

        database_events = (
            load_database_events(
                engine
            )
        )

        print(
            f"Current DB events: "
            f"{len(database_events)}"
        )

        plan = build_sync_plan(
            new_events,
            database_events,
        )

        print_plan(
            plan
        )

        # ----------------------------------------------------
        # Stop automatically if any unsafe conflicts remain.
        # ----------------------------------------------------

        if plan[
            "conflicts"
        ]:

            print()
            print(
                "STOPPED."
            )

            print(
                "There are unresolved conflicts."
            )

            print(
                "NO DATABASE CHANGES WERE MADE."
            )

            return

        # ----------------------------------------------------
        # Create backup BEFORE any database change.
        # ----------------------------------------------------

        backup_path = create_backup(
            database_events
        )

        print()
        print(
            "Backup created:"
        )

        print(
            backup_path
        )

        print()
        print(
            "Expected from our reviewed data:"
        )

        print(
            "45 INSERT / "
            "19 UPDATE / "
            "1 inactive SKIP"
        )

        print()
        print(
            "Nothing has been changed yet."
        )

        print()

        confirmation = input(
            "Type IMPORT exactly "
            "to apply the changes: "
        )

        if (
            confirmation.strip()
            != "IMPORT"
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

        print()
        print(
            "Applying changes..."
        )

        apply_sync(
            engine,
            plan,
        )

        verification = (
            verify_database(
                engine
            )
        )

        print()
        print("=" * 72)

        print(
            "SYNC COMPLETE"
        )

        print("=" * 72)

        print(
            f"Inserted: "
            f"{len(plan['inserts'])}"
        )

        print(
            f"Updated: "
            f"{len(plan['updates'])}"
        )

        print(
            f"Inactive skipped: "
            f"{len(plan['skipped_inactive'])}"
        )

        print()
        print(
            f"Total DB events: "
            f"{verification['total_events']}"
        )

        print(
            f"Active DB events: "
            f"{verification['active_events']}"
        )

        print(
            f"Current/future active events: "
            f"{verification['current_active_events']}"
        )

        print()
        print(
            "NO EVENTS WERE DELETED."
        )

        print(
            "Existing event IDs were preserved."
        )

        print(
            "Existing non-empty official links "
            "were preserved."
        )

        print(
            "Inactive events were NOT reactivated."
        )

        print("=" * 72)

    except Exception as error:

        print()
        print(
            "ERROR:"
        )

        print(
            error
        )

        print()
        print(
            "The write transaction is rolled "
            "back automatically if it failed."
        )

    finally:

        engine.dispose()


if __name__ == "__main__":
    main()