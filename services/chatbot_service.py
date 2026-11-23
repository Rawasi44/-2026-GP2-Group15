"""
AFAQ Gemini-powered chatbot.

Architecture (Phase 2 / 2.3 of the spec):

    User message
        -> Flask backend (this module)
        -> Gemini, with an allowlist of backend "tools" it may call
             (search_events, get_event_details, get_recommendations,
              propose_preferences, propose_save_event)
        -> tool results come ONLY from AFAQ's own MySQL database via
           SQLAlchemy -- Gemini is never allowed to invent event facts,
           run SQL, or run arbitrary code
        -> Gemini's final natural-language reply, grounded in those
           results, is returned to the user

Explicit preferences: this module can only PROPOSE preference categories
(via propose_preferences, which writes nothing). Saving happens only
through routes/chatbot.py's authenticated /chatbot/preferences/confirm
endpoint, which re-validates the proposal and uses Flask-Login's
current_user.user_id -- never a user id supplied by the client or by
Gemini.

Saving one specific event to Favorites follows the identical pattern:
propose_save_event only stores a pending {event_id, title} on this
conversation; the actual Favorite row is only ever created by
routes/chatbot.py's authenticated /chatbot/favorites/confirm endpoint,
again using Flask-Login's current_user.user_id.

Conversation storage: bounded, expiring, server-side, in-process memory
only (see _ConversationStore below). Nothing is written to MySQL and
Flask's session cookie stores only a random conversation id, never
message content.

KNOWN LIMITATION (stated plainly, not glossed over): this module has not
been exercised against the live Gemini API in this environment -- there
is no GEMINI_API_KEY configured here and this sandbox's network allowlist
does not include Google's generative-language endpoint. The google-genai
call shapes below follow the SDK's documented usage as of this writing;
they have been written defensively (every SDK call is wrapped and any
failure degrades to a clear, non-crashing error message) but the exact
request/response handling should be smoke-tested against a real API key
before relying on it.
"""

import os
import re
import time
import uuid
from collections import deque
from datetime import date as date_cls

from sqlalchemy import func, or_

from flask import has_request_context, url_for
from flask_login import current_user

from models.event import Event
from services.recommendation_service import get_recommendations_for_user
from services.preference_service import match_categories, get_known_categories

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:  # pragma: no cover - google-genai not installed
    genai = None
    genai_types = None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

MAX_TOOL_CALLS_PER_MESSAGE = 3
MAX_MESSAGES_PER_CONVERSATION = 20          # bounded history kept in memory
MAX_EVENTS_RETURNED_PER_TOOL_CALL = 10
MAX_CONVERSATIONS = 500                     # overall in-memory cap
CONVERSATION_TTL_SECONDS = 60 * 60          # 1 hour of inactivity -> evicted
MAX_MESSAGES_PER_WINDOW = 20                # simple per-conversation rate limit
RATE_WINDOW_SECONDS = 10 * 60

_client = None
if genai is not None and GEMINI_API_KEY:
    try:
        _client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        _client = None


def is_configured():
    """True only when the SDK is importable, a key is set, and the client
    constructed without error. Never silently falls back to a different
    model or a mock response."""
    return _client is not None


# ---------------------------------------------------------------------------
# Bounded, expiring, server-side conversation store
#
# In-process only. This is explicitly NOT safe across multiple worker
# processes/machines -- a limitation worth knowing about, documented here
# rather than hidden, and acceptable for a single-process student-project
# deployment. It deliberately never touches MySQL.
# ---------------------------------------------------------------------------

_conversations = {}  # conversation_id -> conversation dict


def _new_conversation(user_id):
    return {
        "user_id": user_id,          # None for guests
        "history": deque(maxlen=MAX_MESSAGES_PER_CONVERSATION),
        "pending_preferences": None,  # set by propose_preferences, read by the confirm route
        "pending_favorite": None,     # set by propose_save_event, read by the confirm route
        "message_times": deque(),     # for the simple rate limit
        "last_active": time.time(),
    }


def _evict_stale_conversations():
    now = time.time()
    stale = [
        cid for cid, convo in _conversations.items()
        if now - convo["last_active"] > CONVERSATION_TTL_SECONDS
    ]
    for cid in stale:
        _conversations.pop(cid, None)

    # Hard cap: if still too many, drop the oldest by last_active.
    if len(_conversations) > MAX_CONVERSATIONS:
        ordered = sorted(_conversations.items(), key=lambda kv: kv[1]["last_active"])
        for cid, _convo in ordered[: len(_conversations) - MAX_CONVERSATIONS]:
            _conversations.pop(cid, None)


def get_or_create_conversation(conversation_id, user_id):
    """
    Returns (conversation_id, conversation). If `conversation_id` is
    missing/unknown, a new one is created -- the caller (routes/chatbot.py)
    is responsible for storing the id in the Flask session, never the
    conversation content itself.
    """
    _evict_stale_conversations()

    if conversation_id and conversation_id in _conversations:
        convo = _conversations[conversation_id]
        owner = convo.get("user_id")
        # A conversation that belonged to a signed-in account is never
        # reused by someone else or by a guest after logging out. (A guest
        # who logs in mid-conversation keeps theirs -- owner is None.)
        if owner is None or owner == user_id:
            convo["last_active"] = time.time()
            return conversation_id, convo
        _conversations.pop(conversation_id, None)

    new_id = str(uuid.uuid4())
    convo = _new_conversation(user_id)
    _conversations[new_id] = convo
    return new_id, convo


def _rate_limited(convo):
    now = time.time()
    while convo["message_times"] and now - convo["message_times"][0] > RATE_WINDOW_SECONDS:
        convo["message_times"].popleft()

    if len(convo["message_times"]) >= MAX_MESSAGES_PER_WINDOW:
        return True

    convo["message_times"].append(now)
    return False


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_ARABIC_RANGE = re.compile(r"[؀-ۿ]")


def detect_language(text):
    """Very small heuristic: any Arabic-script character -> "ar", else "en".
    Good enough to pick the reply language and RTL/LTR direction without
    adding a dependency for a two-language, student-scale chatbot."""
    if text and _ARABIC_RANGE.search(text):
        return "ar"
    return "en"


# ---------------------------------------------------------------------------
# Allowlisted backend operations (the ONLY things Gemini can trigger)
# ---------------------------------------------------------------------------

_ARABIC_SEARCH_TRANSLATION = str.maketrans("أإآٱى", "ااااي")
_ARABIC_DIACRITICS = re.compile(r"[\u0610-\u061a\u0640\u064b-\u065f\u0670\u06d6-\u06ed]")
_EVENT_SEARCH_ALIASES = {
    "مهرجان الملك عبد العزيز للصقور": "King Abdulaziz Falconry Festival",
    "مهرجان عبد العزيز للصقور": "King Abdulaziz Falconry Festival",
    "مهرجان الملك عبد العزيز": "King Abdulaziz Falconry Festival",
    "مزاد نادي الصقور السعودي": "Saudi Falcons Club Auction",
    "مزاد المركز الوطني للصقور": "Saudi Falcons Club Auction",
}


def _normalize_arabic_search(text):
    """Normalize common Arabic spelling and spacing in known event names."""
    text = _ARABIC_DIACRITICS.sub("", text).translate(_ARABIC_SEARCH_TRANSLATION)
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split()).replace("عبدالعزيز", "عبد العزيز")


def _serialize_event(event, include_description=False):
    data = {
        "id": event.id,
        "title": event.title,
        "category": event.category,
        "city": event.city,
        "start_date": event.start_date.isoformat() if event.start_date else None,
        "end_date": event.end_date.isoformat() if event.end_date else None,
        "time": event.time,
        "price": event.price,
        "location": event.location,
        "official_link": event.official_link,
    }
    if has_request_context():
        data["details_url"] = url_for("events.event_details", event_id=event.id)
    if include_description:
        data["description"] = (event.description or "")[:600]
    return data


def _active_current_query():
    today = date_cls.today()
    return Event.query.filter(
        Event.is_active == True,
        func.coalesce(Event.end_date, Event.start_date) >= today,
    )


def tool_search_events(
    category=None, keyword=None, date_from=None, date_to=None, max_price=None,
    english_keyword=None,
):
    """
    Searches AFAQ's real event catalog. Returns at most
    MAX_EVENTS_RETURNED_PER_TOOL_CALL active, non-expired events as plain
    dicts -- never invented, never including inactive/expired events.

    date_from/date_to express a requested date RANGE, and an event
    matches when its own [start_date, effective_end_date] interval
    overlaps that range (effective_end_date = COALESCE(end_date,
    start_date)):

        effective_end_date >= date_from   (event hasn't finished before the range starts)
        AND
        start_date <= date_to             (event has started by the time the range ends)

    Either boundary is optional and applied independently when given.
    An unparseable date is a controlled validation failure -- it returns
    an explicit error instead of silently running the search with that
    boundary dropped (which would quietly widen the result set).
    """
    query = _active_current_query()

    if category:
        known = get_known_categories()
        # Only filter by category if it's a real one; otherwise ignore the
        # filter rather than silently returning zero results for a typo.
        matched = match_categories(category) or ({category} if category in known else set())
        if matched:
            query = query.filter(Event.category.in_(matched))

    keyword = (keyword or "").strip()
    english_keyword = (english_keyword or "").strip()
    known_title = _EVENT_SEARCH_ALIASES.get(_normalize_arabic_search(keyword))
    search_terms = dict.fromkeys((keyword, known_title or english_keyword))
    matches = []
    for term in search_terms:
        if term:
            # Escape LIKE wildcards so the input remains a literal phrase.
            escaped = term.replace("!", "!!").replace("%", "!%").replace("_", "!_")
            pattern = f"%{escaped}%"
            matches.extend((
                Event.title.ilike(pattern, escape="!"),
                Event.description.ilike(pattern, escape="!"),
            ))
    if matches:
        query = query.filter(or_(*matches))

    parsed_date_from = None
    parsed_date_to = None

    if date_from:
        try:
            parsed_date_from = date_cls.fromisoformat(date_from)
        except (TypeError, ValueError):
            return {"error": "invalid_date", "field": "date_from", "value": date_from}

    if date_to:
        try:
            parsed_date_to = date_cls.fromisoformat(date_to)
        except (TypeError, ValueError):
            return {"error": "invalid_date", "field": "date_to", "value": date_to}

    if (
        parsed_date_from is not None
        and parsed_date_to is not None
        and parsed_date_from > parsed_date_to
    ):
        return {"error": "invalid_date_range", "date_from": date_from, "date_to": date_to}

    effective_end_date = func.coalesce(Event.end_date, Event.start_date)

    if parsed_date_from is not None:
        query = query.filter(effective_end_date >= parsed_date_from)

    if parsed_date_to is not None:
        query = query.filter(Event.start_date <= parsed_date_to)

    events = query.order_by(Event.start_date.asc()).limit(MAX_EVENTS_RETURNED_PER_TOOL_CALL).all()

    if max_price:
        # Price is a free-text field ("75 SAR", "", None) -- only keep
        # events where we can confidently parse a number under the budget.
        # Never assume a blank/unparseable price means free.
        filtered = []
        for event in events:
            digits = re.findall(r"\d+", event.price or "")
            if digits and int(digits[0]) <= int(max_price):
                filtered.append(event)
        events = filtered

    return {
        "count": len(events),
        "events": [_serialize_event(e) for e in events],
    }


def tool_get_event_details(event_id):
    """Returns one validated event by id, or an explicit not-found/
    unavailable result -- never fabricated data."""
    try:
        event_id = int(event_id)
    except (TypeError, ValueError):
        return {"found": False, "reason": "invalid_event_id"}

    event = Event.query.get(event_id)
    if event is None:
        return {"found": False, "reason": "not_found"}

    from datetime import date
    today = date.today()
    effective_end = event.end_date or event.start_date
    if not event.is_active:
        return {"found": False, "reason": "inactive"}
    if effective_end < today:
        return {"found": False, "reason": "expired"}

    return {"found": True, "event": _serialize_event(event, include_description=True)}


def tool_get_personalized_recommendations(authenticated_user_id, limit=6):
    """Only ever uses the SERVER-SIDE authenticated user id -- the
    `authenticated_user_id` argument is supplied by routes/chatbot.py from
    Flask-Login, never taken from anything Gemini returns."""
    if not authenticated_user_id:
        return {"authenticated": False, "events": []}

    limit = max(1, min(int(limit or 6), MAX_EVENTS_RETURNED_PER_TOOL_CALL))
    events = get_recommendations_for_user(authenticated_user_id, limit=limit)
    return {
        "authenticated": True,
        "count": len(events),
        "events": [_serialize_event(e) for e in events],
    }


def tool_propose_preferences(conversation, raw_text):
    """
    Extracts canonical categories mentioned in `raw_text` and stores them
    as a PENDING proposal on this conversation only -- nothing is written
    to the database here. The frontend shows a confirmation UI; only a
    subsequent, authenticated call to /chatbot/preferences/confirm can
    turn this into a saved UserPreference row.
    """
    if not conversation.get("user_id"):
        # Guests have no account to save preferences to.
        conversation["needs_account"] = True
        return {"proposed": [], "reason": "not_authenticated"}

    categories = sorted(match_categories(raw_text))
    if not categories:
        return {"proposed": []}

    conversation["pending_preferences"] = categories
    return {"proposed": categories}


def tool_propose_save_event(conversation, event_id):
    """
    Proposes saving ONE SPECIFIC event to the user's Favorites -- this is
    distinct from propose_preferences, which only proposes a general
    category interest. Like propose_preferences, this writes NOTHING by
    itself: it only stores a pending proposal on this conversation, and
    the frontend shows a confirmation UI. Only a subsequent, authenticated
    call to /chatbot/favorites/confirm actually creates the Favorite row
    (using Flask-Login's current_user.user_id, never a client-supplied id).
    """
    try:
        event_id = int(event_id)
    except (TypeError, ValueError):
        return {"proposed": False, "reason": "invalid_event_id"}

    if not conversation.get("user_id"):
        conversation["needs_account"] = True
        return {"proposed": False, "reason": "not_authenticated"}

    event = Event.query.get(event_id)
    if event is None or not event.is_active:
        return {"proposed": False, "reason": "not_found"}

    conversation["pending_favorite"] = {
        "event_id": event.id,
        "title": event.title,
    }
    return {"proposed": True, "event_id": event.id, "title": event.title}


ALLOWED_TOOLS = {
    "search_events",
    "get_event_details",
    "get_recommendations",
    "propose_preferences",
    "propose_save_event",
}


# ---------------------------------------------------------------------------
# Gemini function-calling declarations
# ---------------------------------------------------------------------------

def _build_tool_declarations():
    if genai_types is None:
        return None

    return genai_types.Tool(
        function_declarations=[
            genai_types.FunctionDeclaration(
                name="search_events",
                description=(
                    "Search AFAQ's real, current event catalog in Riyadh. "
                    "Always use this instead of guessing event information."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "Event category, e.g. Entertainment & Sports"},
                        "keyword": {
                            "type": "string",
                            "description": "Event name or short phrase from the user, in their original language.",
                        },
                        "english_keyword": {
                            "type": "string",
                            "description": "For an Arabic keyword, provide the English event name or a short English phrase.",
                        },
                        "date_from": {"type": "string", "description": "ISO date (YYYY-MM-DD), events starting on/after this date"},
                        "date_to": {"type": "string", "description": "ISO date (YYYY-MM-DD), events ending on/before this date"},
                        "max_price": {"type": "integer", "description": "Maximum budget in SAR, only applied when the event's price can be confidently parsed"},
                    },
                },
            ),
            genai_types.FunctionDeclaration(
                name="get_event_details",
                description="Get full details for one specific AFAQ event by its database id.",
                parameters={
                    "type": "object",
                    "properties": {"event_id": {"type": "integer"}},
                    "required": ["event_id"],
                },
            ),
            genai_types.FunctionDeclaration(
                name="get_recommendations",
                description=(
                    "Get this logged-in user's personalized AFAQ recommendations. "
                    "Only works for authenticated users; returns authenticated=false otherwise."
                ),
                parameters={
                    "type": "object",
                    "properties": {"limit": {"type": "integer"}},
                },
            ),
            genai_types.FunctionDeclaration(
                name="propose_preferences",
                description=(
                    "Call this when the user states or confirms an interest in specific event "
                    "categories (e.g. 'I like sports and theatre'), so AFAQ can offer to save "
                    "them. This does NOT save anything by itself."
                ),
                parameters={
                    "type": "object",
                    "properties": {"raw_text": {"type": "string", "description": "The user's own words describing their interest"}},
                    "required": ["raw_text"],
                },
            ),
            genai_types.FunctionDeclaration(
                name="propose_save_event",
                description=(
                    "Call this when the user asks to save, add, bookmark, or favorite ONE "
                    "SPECIFIC event you already showed them (e.g. 'save it', 'add this one to "
                    "my favorites') -- use the event_id from a prior search_events / "
                    "get_event_details / get_recommendations result for that event. This is "
                    "different from propose_preferences, which is for a general category "
                    "interest, not one specific event. This does NOT save anything by itself."
                ),
                parameters={
                    "type": "object",
                    "properties": {"event_id": {"type": "integer", "description": "The AFAQ event id to save"}},
                    "required": ["event_id"],
                },
            ),
        ]
    )


SYSTEM_INSTRUCTION_TEMPLATE = """You are the AFAQ event discovery assistant for Riyadh events.

Rules you must always follow:
- Every event fact you state (title, date, time, price, location, link) must come from a
  search_events / get_event_details / get_recommendations tool call. Never invent events,
  prices, dates, times, or links.
- Never claim an event exists if no tool call confirmed it.
- The catalog often stores English titles. For an Arabic event name, pass the name alone as
  keyword and its English title or a concise English phrase as english_keyword.
  If the first search returns no results, retry with a more distinctive English phrase
  before saying the event was not found. Apply date filters only when the user asks for them.
- For a link inside AFAQ, use the exact details_url returned by the event tool. Use
  official_link when the user asks for the organizer's link.
- Never say or imply that a missing price means the event is free.
- Never claim to have added, booked, or modified anything in the user's calendar -- AFAQ does
  not implement ticket booking, and calendar changes only happen when the user explicitly saves
  a suggestion themselves.
- If the user describes an interest in specific event categories (not one named event), call
  propose_preferences with their own words, then ask them to confirm before AFAQ saves it. Never
  claim you already saved a preference -- only a follow-up confirmation by the user actually
  saves it.
- If the user asks to save/add/bookmark/favorite ONE SPECIFIC event you already showed them, call
  propose_save_event with that event's id (from your last search_events / get_event_details /
  get_recommendations result for it) -- do NOT call propose_preferences for this, and do not just
  tell them to go browse the app themselves when they clearly asked you to save that event. Then
  ask them to confirm. Never claim the event is already saved -- only the user's follow-up
  confirmation actually saves it.
- If a tool result says reason "not_authenticated", the user is a guest with no account: tell
  them they need an account (sign up or log in) to save favorites or preferences.
- Reply in whichever language the user's latest message is written in (English or Arabic). Keep
  Arabic replies in Arabic script with correct grammar; do not mix scripts within one reply unless
  quoting an event title that is itself in the other language.
- Stay concise, friendly, and focused on AFAQ and Riyadh events. If asked something unrelated,
  briefly redirect back to event discovery.
- Today's date in Asia/Riyadh is {today}. Use it to resolve relative dates like "this weekend" or
  "next week" into concrete date_from/date_to values before calling search_events.
- Write the way a knowledgeable friend would text, not like a legal disclaimer. State a needed
  caveat (e.g. that a time is an estimate, or that AFAQ doesn't book tickets) once, briefly, in
  your own words -- never repeat the same boilerplate reminder in every reply, and never stack
  multiple caveats back-to-back when one sentence covers it. Skip a caveat entirely once you've
  already told the user that same thing earlier in this conversation. Vary your phrasing and
  sentence rhythm reply to reply so answers don't all sound like they came from the same template.
"""


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def _run_tool(name, args, conversation):
    if name not in ALLOWED_TOOLS:
        return {"error": "tool_not_allowed"}

    try:
        if name == "search_events":
            return tool_search_events(
                category=args.get("category"),
                keyword=args.get("keyword"),
                english_keyword=args.get("english_keyword"),
                date_from=args.get("date_from"),
                date_to=args.get("date_to"),
                max_price=args.get("max_price"),
            )
        if name == "get_event_details":
            return tool_get_event_details(args.get("event_id"))
        if name == "get_recommendations":
            return tool_get_personalized_recommendations(
                conversation.get("user_id"),
                limit=args.get("limit", 6),
            )
        if name == "propose_preferences":
            return tool_propose_preferences(conversation, args.get("raw_text", ""))
        if name == "propose_save_event":
            return tool_propose_save_event(conversation, args.get("event_id"))
    except Exception as exc:  # defensive: a tool must never crash the chat turn
        return {"error": "tool_failed", "detail": str(exc)[:200]}

    return {"error": "tool_not_allowed"}


def _fallback_reply(lang, reason):
    messages = {
        "missing_api_key": {
            "en": "The AFAQ assistant isn't configured yet. Please try again later.",
            "ar": "مساعد أفاق غير مُفعّل حالياً. يرجى المحاولة لاحقاً.",
        },
        "unavailable": {
            "en": "The assistant is temporarily unavailable. Please try again in a moment.",
            "ar": "المساعد غير متاح حالياً. حاول مرة أخرى بعد قليل.",
        },
        "needs_account": {
            "en": "You don't have an account yet, so I can't save that for you. Sign up or log in, then ask me again.",
            "ar": "ما عندك حساب بعد، فما أقدر أحفظ هذا لك. سجّل حساب أو سجّل دخولك، وبعدها اطلب مني مرة ثانية.",
        },
        "rate_limited": {
            "en": "You've sent a lot of messages in a short time. Please wait a bit before continuing.",
            "ar": "لقد أرسلت رسائل كثيرة في وقت قصير. الرجاء الانتظار قليلاً قبل المواصلة.",
        },
    }
    return messages.get(reason, messages["unavailable"]).get(lang, messages["unavailable"]["en"])


def handle_message(conversation_id, user, message):
    """
    Main entry point, called by routes/chatbot.py.

    `user` is the Flask-Login current_user (or None/AnonymousUserMixin for
    guests) -- only its authenticated user_id is ever trusted and passed
    down to tools.

    Returns a plain dict, JSON-serializable:
        {
            "conversation_id": str,
            "reply": str,
            "lang": "en" | "ar",
            "pending_preferences": [str, ...] or None,
        }
    """
    user_id = getattr(user, "user_id", None) if getattr(user, "is_authenticated", False) else None
    conversation_id, convo = get_or_create_conversation(conversation_id, user_id)
    convo["user_id"] = user_id  # keep in sync if a guest logs in mid-conversation

    lang = detect_language(message)

    if _rate_limited(convo):
        return {
            "conversation_id": conversation_id,
            "reply": _fallback_reply(lang, "rate_limited"),
            "lang": lang,
            "pending_preferences": convo.get("pending_preferences"),
            "pending_favorite": convo.get("pending_favorite"),
        }

    if not is_configured():
        return {
            "conversation_id": conversation_id,
            "reply": _fallback_reply(lang, "missing_api_key"),
            "lang": lang,
            "pending_preferences": convo.get("pending_preferences"),
            "pending_favorite": convo.get("pending_favorite"),
        }

    from datetime import datetime, timezone, timedelta
    riyadh_today = (datetime.now(timezone.utc) + timedelta(hours=3)).date().isoformat()
    system_instruction = SYSTEM_INSTRUCTION_TEMPLATE.format(today=riyadh_today)

    convo["history"].append({"role": "user", "text": message})
    convo["needs_account"] = False

    contents = []
    for turn in convo["history"]:
        contents.append(
            genai_types.Content(
                role="user" if turn["role"] == "user" else "model",
                parts=[genai_types.Part.from_text(text=turn["text"])],
            )
        )

    tool = _build_tool_declarations()
    reply_text = None

    try:
        for _ in range(MAX_TOOL_CALLS_PER_MESSAGE + 1):
            response = _client.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config=genai_types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    tools=[tool] if tool else None,
                    temperature=0.3,
                ),
            )

            candidate = response.candidates[0] if getattr(response, "candidates", None) else None
            function_calls = [
                part.function_call
                for part in (candidate.content.parts if candidate and candidate.content else [])
                if getattr(part, "function_call", None)
            ]

            if not function_calls:
                reply_text = getattr(response, "text", None) or ""
                break

            # Echo the model's own function-call turn back, then append
            # each tool's result as a function_response part.
            contents.append(candidate.content)
            response_parts = []
            for call in function_calls:
                result = _run_tool(call.name, dict(call.args or {}), convo)
                response_parts.append(
                    genai_types.Part.from_function_response(name=call.name, response=result)
                )
            contents.append(genai_types.Content(role="user", parts=response_parts))

        if reply_text is None:
            reply_text = (
                "I wasn't able to finish that request just now. Could you rephrase it?"
                if lang == "en"
                else "لم أتمكن من إنهاء طلبك الآن. هل يمكنك إعادة صياغته؟"
            )

    except Exception:
        # Network error, invalid credentials, quota/rate limit, unavailable
        # model, or an unexpected response shape all land here. Never
        # fabricate a reply -- fail to a clear, generic message instead.
        reply_text = _fallback_reply(lang, "unavailable")

    # A guest asked to save a favorite/preference: say so plainly and the
    # same way every time, instead of leaving it to the model's wording.
    if convo.get("needs_account"):
        reply_text = _fallback_reply(lang, "needs_account")
        convo["pending_preferences"] = None
        convo["pending_favorite"] = None

    convo["history"].append({"role": "model", "text": reply_text})

    return {
        "conversation_id": conversation_id,
        "reply": reply_text,
        "lang": lang,
        "pending_preferences": convo.get("pending_preferences"),
        "pending_favorite": convo.get("pending_favorite"),
    }


def get_pending_preferences(conversation_id):
    convo = _conversations.get(conversation_id)
    if not convo:
        return None
    return convo.get("pending_preferences")


def clear_pending_preferences(conversation_id):
    convo = _conversations.get(conversation_id)
    if convo:
        convo["pending_preferences"] = None


def get_pending_favorite(conversation_id):
    convo = _conversations.get(conversation_id)
    if not convo:
        return None
    return convo.get("pending_favorite")


def clear_pending_favorite(conversation_id):
    convo = _conversations.get(conversation_id)
    if convo:
        convo["pending_favorite"] = None
