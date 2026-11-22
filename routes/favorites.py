from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user

from extensions import db
from models.event import Event
from models.favorite import Favorite

favorites_bp = Blueprint("favorites", __name__)


def _wants_json():
    """
    True when this request came from the AJAX favorite-toggle script
    (static/js/favorites.js), not a normal full-page form submission.

    The JS always sends this header, so plain browser form posts
    (JS disabled, or any other caller) keep the original
    redirect+flash behavior untouched -- this is additive, not a
    replacement of the existing route contract.
    """
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


@favorites_bp.route("/favorites")
@login_required
def favorites_page():
    # Historical Favorite rows are preserved even after their event is
    # soft-deleted, but inactive events are filtered out of this
    # user-facing list (their rows remain in the database untouched).
    favorites = (
        Favorite.query
        .filter_by(user_id=current_user.user_id)
        .join(Event, Favorite.event_id == Event.id)
        .filter(Event.is_active == True)
        .order_by(Favorite.created_at.desc())
        .all()
    )
    return render_template("favorites.html", favorites=favorites)


@favorites_bp.route("/favorites/<int:event_id>/toggle", methods=["POST"])
@login_required
def toggle_favorite(event_id):
    event = Event.query.get_or_404(event_id)

    existing = Favorite.query.filter_by(user_id=current_user.user_id, event_id=event_id).first()
    ajax = _wants_json()

    if existing:
        db.session.delete(existing)
        db.session.commit()
        message, category, is_favorited = "Removed from your favorites.", "success", False
        next_url = request.form.get("next") or url_for("events.event_details", event_id=event_id)
    elif not event.is_active:
        message, category, is_favorited = (
            "This event is no longer available and can't be added to favorites.",
            "warning",
            False,
        )
        next_url = request.form.get("next") or url_for("events.events_page")
    else:
        db.session.add(Favorite(user_id=current_user.user_id, event_id=event_id))
        db.session.commit()
        message, category, is_favorited = "Added to your favorites.", "success", True
        next_url = request.form.get("next") or url_for("events.event_details", event_id=event_id)

    if ajax:
        return jsonify(
            {
                "ok": category != "warning",
                "is_favorited": is_favorited,
                "message": message,
                "event_id": event_id,
            }
        )

    flash(message, category)
    return redirect(next_url)
