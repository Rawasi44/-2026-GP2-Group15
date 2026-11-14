"""
Demonstration-data seed script (Phase 6).

WHAT THIS DOES
---------------
Creates (or re-uses, idempotently) five demonstration USER accounts and
populates their Ratings / Favorites / Personal Calendar entries using
REAL, EXISTING Event rows already in the database. It never creates a
new Event record.

SAFETY
------
This script refuses to run unless BOTH are true:
  1. The environment variable ALLOW_DEMO_SEED=true is set.
  2. The resolved SQLALCHEMY_DATABASE_URI does not contain the substring
     "prod" (case-insensitive).

That second check is a convention-based guard, not a real environment
separation mechanism -- this project has a single hardcoded database URL
in config.py (afaq_db) with no distinct dev/staging/prod databases, so
there is no infrastructure-level guarantee to lean on. Treat this script
as something you run against a database you know is safe to write demo
data into, and take a `mysqldump` backup first regardless.

NOT EXECUTED BY CLAUDE. This file is prepared for your review and must
be run by you, after you've reviewed it, against a database you've
chosen deliberately.

USAGE
-----
    ALLOW_DEMO_SEED=true python seed_demo_users.py

Generated demo-account passwords are written once to
`demo_users_credentials.local.txt` next to this script (never printed to
stdout, never committed -- add that filename to .gitignore). Re-running
the script is safe: existing demo users/interactions are detected and
skipped rather than duplicated, and no existing (real) user, rating,
favorite, or calendar entry is ever modified.
"""

import os
import secrets
import sys

DEMO_MARKER = "afaq.demo+"  # email prefix used to recognize demo accounts

DEMO_USERS = [
    {
        "slug": "demo_sports",
        "fullname": "Demo Sports Enthusiast",
        "categories": ["Entertainment & Sports"],
        "profile": "sports",
    },
    {
        "slug": "demo_arts",
        "fullname": "Demo Arts and Culture",
        "categories": ["Exhibitions & Conferences", "Culture & Community"],
        "profile": "arts",
    },
    {
        "slug": "demo_music",
        "fullname": "Demo Music and Entertainment",
        "categories": ["Entertainment & Sports", "Festivals & Celebrations"],
        "profile": "music",
    },
    {
        "slug": "demo_mixed",
        "fullname": "Demo Mixed Interests",
        "categories": ["Entertainment & Sports", "Exhibitions & Conferences", "Festivals & Celebrations"],
        "profile": "mixed",
    },
    {
        "slug": "demo_coldstart",
        "fullname": "Demo New User",
        "categories": [],
        "profile": "cold_start",
    },
]

MAX_EVENTS_PER_CATEGORY = 3


def _guard():
    if os.getenv("ALLOW_DEMO_SEED", "").lower() != "true":
        print("Refusing to run: set ALLOW_DEMO_SEED=true to confirm this is a safe database.")
        sys.exit(1)

    from config import Config
    if "prod" in Config.SQLALCHEMY_DATABASE_URI.lower():
        print("Refusing to run: the configured database URI looks like production.")
        sys.exit(1)


def _random_password():
    # 16 random URL-safe characters -- not meant to be memorable, only to
    # satisfy the app's normal password hashing path for a labeled test
    # account that nobody needs to type by hand.
    return secrets.token_urlsafe(12)


def run():
    _guard()

    from app import create_app
    from extensions import db
    from models.user import User
    from models.event import Event
    from models.favorite import Favorite
    from models.rating import Rating
    from models.calendar_event import CalendarEvent

    app = create_app()
    credentials = []
    summary_lines = []

    with app.app_context():
        for spec in DEMO_USERS:
            email = f"{DEMO_MARKER}{spec['slug']}@example.com"
            user = User.query.filter_by(email=email).first()

            if user is None:
                password = _random_password()
                user = User(
                    fullname=spec["fullname"],
                    email=email,
                    role="user",
                    is_verified=True,  # labeled test account: skip the real email-verification flow deliberately
                )
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                credentials.append((email, password))
                summary_lines.append(f"Created user: {email} (id={user.user_id})")
            else:
                summary_lines.append(f"Reused existing user: {email} (id={user.user_id})")

            if not spec["categories"]:
                summary_lines.append(f"  {spec['slug']}: no interactions added (cold-start demo user)")
                continue

            target_events = []
            for category in spec["categories"]:
                matches = (
                    Event.query
                    .filter_by(category=category, is_active=True)
                    .limit(MAX_EVENTS_PER_CATEGORY)
                    .all()
                )
                target_events.extend(matches)

            if not target_events:
                summary_lines.append(
                    f"  {spec['slug']}: no active events found for {spec['categories']} -- skipped interactions"
                )
                continue

            added_ratings = added_favorites = added_calendar = 0

            for index, event in enumerate(target_events):
                if not Rating.query.filter_by(user_id=user.user_id, event_id=event.id).first():
                    db.session.add(Rating(user_id=user.user_id, event_id=event.id, value=5 if index == 0 else 4))
                    added_ratings += 1

                if not Favorite.query.filter_by(user_id=user.user_id, event_id=event.id).first():
                    db.session.add(Favorite(user_id=user.user_id, event_id=event.id))
                    added_favorites += 1

            # One calendar entry (the first matched event), only for a
            # scheduled_date that is actually valid for that event:
            #
            #     max(today, event.start_date) <= scheduled_date <= (event.end_date or event.start_date)
            #
            # If no such date exists (e.g. a single-day event that
            # already started and ends today, or has fully ended), the
            # calendar interaction is skipped rather than inserting an
            # out-of-range row.
            from datetime import date
            first_event = target_events[0]

            if (
                first_event.start_date
                and not CalendarEvent.query.filter_by(
                    user_id=user.user_id, event_id=first_event.id
                ).first()
            ):
                today = date.today()
                effective_end = first_event.end_date or first_event.start_date
                earliest_valid = max(today, first_event.start_date)

                if earliest_valid <= effective_end:
                    db.session.add(
                        CalendarEvent(
                            user_id=user.user_id,
                            event_id=first_event.id,
                            scheduled_date=earliest_valid,
                        )
                    )
                    added_calendar += 1

            db.session.commit()
            summary_lines.append(
                f"  {spec['slug']}: +{added_ratings} ratings, +{added_favorites} favorites, "
                f"+{added_calendar} calendar entries across {len(target_events)} real events"
            )

    if credentials:
        cred_path = os.path.join(os.path.dirname(__file__), "demo_users_credentials.local.txt")
        with open(cred_path, "w") as f:
            f.write("AFAQ demo account credentials -- do not commit this file.\n\n")
            for email, password in credentials:
                f.write(f"{email}\t{password}\n")
        summary_lines.append(f"\nNew credentials written to: {cred_path}")

    print("\n".join(summary_lines))


if __name__ == "__main__":
    run()
