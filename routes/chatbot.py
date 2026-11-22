"""
Chatbot routes.

/chatbot/message is open to guests and authenticated users alike (the
chatbot itself works for everyone; only persistent preference storage
requires login, enforced separately below).

The Flask session only ever stores a random conversation id
(`chat_conversation_id`) -- never message content, matching Phase 3.4 /
Phase 8's "do not store full conversations in Flask's client-side session
cookie" requirement. The actual message history lives in
services.chatbot_service's bounded, expiring, server-side store.
"""

from flask import Blueprint, jsonify, request, session
from flask_login import current_user, login_required

from extensions import db
from models.event import Event
from models.favorite import Favorite
from services import chatbot_service
from services.preference_service import (
    list_preferences,
    save_preferences,
    remove_preference,
)


chatbot_bp = Blueprint("chatbot", __name__, url_prefix="/chatbot")


@chatbot_bp.route("/message", methods=["POST"])
def send_message():
    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()

    if not message:
        return jsonify({"error": "empty_message"}), 400

    if len(message) > 1000:
        message = message[:1000]

    conversation_id = session.get("chat_conversation_id")
    result = chatbot_service.handle_message(conversation_id, current_user, message)

    # Only the id is kept in the session, never the conversation content.
    session["chat_conversation_id"] = result["conversation_id"]

    return jsonify(result)


@chatbot_bp.route("/preferences", methods=["GET"])
@login_required
def get_preferences():
    return jsonify({"preferences": list_preferences(current_user.user_id)})


@chatbot_bp.route("/preferences/confirm", methods=["POST"])
@login_required
def confirm_preferences():
    """
    Saves whatever categories the chatbot proposed for THIS browser's
    conversation. Deliberately does not accept a list of categories from
    the request body -- the server re-reads its own stored proposal for
    this conversation_id, so a client can't inject arbitrary categories
    by editing the request.
    """
    conversation_id = session.get("chat_conversation_id")
    pending = chatbot_service.get_pending_preferences(conversation_id) if conversation_id else None

    if not pending:
        return jsonify({"saved": [], "reason": "nothing_pending"}), 400

    saved = save_preferences(current_user.user_id, pending, source="chatbot_explicit")
    chatbot_service.clear_pending_preferences(conversation_id)

    return jsonify({"saved": saved})


@chatbot_bp.route("/preferences/decline", methods=["POST"])
@login_required
def decline_preferences():
    conversation_id = session.get("chat_conversation_id")
    if conversation_id:
        chatbot_service.clear_pending_preferences(conversation_id)
    return jsonify({"declined": True})


@chatbot_bp.route("/preferences/remove", methods=["POST"])
@login_required
def remove_preference_route():
    payload = request.get_json(silent=True) or {}
    category = (payload.get("category") or "").strip()

    if not category:
        return jsonify({"removed": False, "reason": "missing_category"}), 400

    removed = remove_preference(current_user.user_id, category)
    return jsonify({"removed": removed})


@chatbot_bp.route("/favorites/confirm", methods=["POST"])
@login_required
def confirm_favorite():
    """
    Saves the ONE event the chatbot proposed for THIS browser's
    conversation. Like confirm_preferences above, this deliberately does
    not accept an event_id from the request body -- it re-reads its own
    stored proposal for this conversation_id, so a client can't save an
    arbitrary event by editing the request. Re-checks the event is still
    active before saving (it may have been deactivated since the
    conversation turn that proposed it).
    """
    conversation_id = session.get("chat_conversation_id")
    pending = chatbot_service.get_pending_favorite(conversation_id) if conversation_id else None

    if not pending:
        return jsonify({"saved": False, "reason": "nothing_pending"}), 400

    event_id = pending["event_id"]
    event = Event.query.get(event_id)

    if event is None or not event.is_active:
        chatbot_service.clear_pending_favorite(conversation_id)
        return jsonify({"saved": False, "reason": "event_unavailable"}), 400

    existing = Favorite.query.filter_by(user_id=current_user.user_id, event_id=event_id).first()
    if not existing:
        db.session.add(Favorite(user_id=current_user.user_id, event_id=event_id))
        db.session.commit()

    chatbot_service.clear_pending_favorite(conversation_id)
    return jsonify({"saved": True, "event_id": event_id, "title": event.title})


@chatbot_bp.route("/favorites/decline", methods=["POST"])
@login_required
def decline_favorite():
    conversation_id = session.get("chat_conversation_id")
    if conversation_id:
        chatbot_service.clear_pending_favorite(conversation_id)
    return jsonify({"declined": True})
