"""
Explicit-preference support: bilingual category normalization, plus the
save/remove/list operations for UserPreference.

Canonical category names are never invented here -- they are always the
literal Event.category values already in use in the database, so a saved
preference can never point at a category that has no events behind it.

Every write here takes `user_id` as a plain argument and trusts it
completely: callers (routes/chatbot.py) must only ever pass
current_user.user_id from an authenticated Flask-Login session, never a
value read from request JSON, a form field, or anything Gemini returned.
"""

from extensions import db
from models.event import Event
from models.user_preference import UserPreference


# Keyword/alias -> canonical category. The canonical side must exactly
# match a real Event.category string. Matching is done on a lowercased
# copy of the user's message.
#
# This is a small, explainable lookup table -- not a general-purpose NLP
# classifier -- which fits AFAQ's actual (small) category set and keeps
# the mapping easy to audit and extend.
CATEGORY_ALIASES = {
    # Entertainment & Sports
    "sports": "Entertainment & Sports",
    "sport": "Entertainment & Sports",
    "football": "Entertainment & Sports",
    "soccer": "Entertainment & Sports",
    "entertainment": "Entertainment & Sports",
    "music": "Entertainment & Sports",
    "concert": "Entertainment & Sports",
    "concerts": "Entertainment & Sports",
    "رياضة": "Entertainment & Sports",
    "رياضية": "Entertainment & Sports",
    "كرة": "Entertainment & Sports",
    "كرة القدم": "Entertainment & Sports",
    "ترفيه": "Entertainment & Sports",
    "موسيقى": "Entertainment & Sports",
    "حفلات": "Entertainment & Sports",

    # Exhibitions & Conferences
    "exhibition": "Exhibitions & Conferences",
    "exhibitions": "Exhibitions & Conferences",
    "conference": "Exhibitions & Conferences",
    "conferences": "Exhibitions & Conferences",
    "theatre": "Exhibitions & Conferences",
    "theater": "Exhibitions & Conferences",
    "معارض": "Exhibitions & Conferences",
    "مؤتمرات": "Exhibitions & Conferences",
    "مسرح": "Exhibitions & Conferences",
    "مسرحيات": "Exhibitions & Conferences",

    # Family & Kids
    "family": "Family & Kids",
    "kids": "Family & Kids",
    "children": "Family & Kids",
    "عائلية": "Family & Kids",
    "عائلة": "Family & Kids",
    "أطفال": "Family & Kids",

    # Culture & Community
    "culture": "Culture & Community",
    "community": "Culture & Community",
    "ثقافة": "Culture & Community",
    "ثقافية": "Culture & Community",
    "مجتمع": "Culture & Community",

    # Festivals & Celebrations
    "festival": "Festivals & Celebrations",
    "festivals": "Festivals & Celebrations",
    "celebration": "Festivals & Celebrations",
    "celebrations": "Festivals & Celebrations",
    "مهرجان": "Festivals & Celebrations",
    "مهرجانات": "Festivals & Celebrations",
    "احتفالات": "Festivals & Celebrations",
}


def get_known_categories():
    """Distinct, non-blank Event.category values actually in the database."""
    rows = db.session.query(Event.category).distinct().all()
    return {row[0].strip() for row in rows if row[0] and row[0].strip()}


def match_categories(text):
    """
    Returns the set of canonical categories mentioned in `text` (English
    or Arabic), restricted to categories that actually exist in the
    database right now.

    An alias whose canonical target is not currently present in
    Event.category is silently dropped rather than proposed, so the
    chatbot can never offer to save a "preference" for a category that
    has no events in AFAQ.
    """
    if not text:
        return set()

    normalized = " " + text.lower().strip() + " "
    known = get_known_categories()

    matched = set()
    for alias, canonical in CATEGORY_ALIASES.items():
        if alias in normalized and canonical in known:
            matched.add(canonical)

    return matched


def list_preferences(user_id):
    """Confirmed preference categories for this user, as plain strings."""
    if not user_id:
        return []

    rows = (
        UserPreference.query
        .filter_by(user_id=user_id)
        .order_by(UserPreference.category)
        .all()
    )
    return [row.category for row in rows]


def save_preferences(user_id, categories, source="chatbot_explicit"):
    """
    Persists confirmed categories for `user_id`. Categories are validated
    against the live database category set again here (not only at
    proposal time), so a stale or tampered confirmation request can't
    create a preference for a category that no longer exists.

    Re-confirming an already-saved category updates it in place rather
    than creating a duplicate row (the unique constraint on
    (user_id, category) also enforces this at the database level).

    Never touches Favorite, Rating, CalendarEvent, or Search rows.
    """
    if not user_id or not categories:
        return []

    known = get_known_categories()
    valid_categories = [c for c in categories if c in known]
    if not valid_categories:
        return []

    saved = []
    for category in valid_categories:
        existing = (
            UserPreference.query
            .filter_by(user_id=user_id, category=category)
            .first()
        )
        if existing:
            existing.source = source
        else:
            existing = UserPreference(
                user_id=user_id,
                category=category,
                source=source,
            )
            db.session.add(existing)
        saved.append(category)

    db.session.commit()
    return saved


def remove_preference(user_id, category):
    """Removes one confirmed preference for this user, if it exists."""
    if not user_id or not category:
        return False

    row = (
        UserPreference.query
        .filter_by(user_id=user_id, category=category)
        .first()
    )
    if not row:
        return False

    db.session.delete(row)
    db.session.commit()
    return True
