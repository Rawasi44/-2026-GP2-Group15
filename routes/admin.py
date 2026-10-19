from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from extensions import db
from models.event import Event
from datetime import datetime
from functools import wraps
import os
from flask import current_app
from werkzeug.utils import secure_filename
admin_bp = Blueprint('admin', __name__)


# ─── Access guard ─────────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            flash('Please log in to access this page.')
            return redirect(url_for('auth.login'))
        if current_user.role != 'admin':
            flash('You are not authorized to access the admin page.')
            return redirect(url_for('events.home'))
        return f(*args, **kwargs)
    return decorated


# ─── Admin dashboard ──────────────────────────────────────────────────────────

@admin_bp.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    # Only fetch events entered manually by admin (source = "admin")
    # Soft-deleted events are hidden from the normal dashboard, but their
    # row and all historical interaction data remain in the database.
    admin_events = (
        Event.query
        .filter(Event.is_active == True)
        .order_by(Event.start_date.desc())
        .all()
    )
    return render_template('admin/index.html', events=admin_events)


# ─── Admin profile ────────────────────────────────────────────────────────────

@admin_bp.route('/admin/profile')
@login_required
@admin_required
def profile():
    return render_template('admin/profile.html', user=current_user)


# ─── Add Event ────────────────────────────────────────────────────────────────
@admin_bp.route('/admin/add-event', methods=['GET', 'POST'])
@login_required
@admin_required
def add_event():
    errors = {}

    if request.method == 'POST':

        title = request.form.get('title', '').strip()
        category = request.form.get('category', '').strip()
        city = "Riyadh"
        date_str = request.form.get('date', '').strip()
        time_str = request.form.get('time', '').strip()
        description = request.form.get('description', '').strip()

        # Image Upload
        image_file = request.files.get('image')
        image = ""

        if image_file and image_file.filename != "":
            filename = secure_filename(image_file.filename)

            upload_folder = os.path.join(
                current_app.root_path,
                "static",
                "uploads"
            )

            os.makedirs(upload_folder, exist_ok=True)

            image_path = os.path.join(upload_folder, filename)

            image_file.save(image_path)

            image = f"/static/uploads/{filename}"

        official_link = request.form.get('official_link', '').strip()
        price = request.form.get('price', '').strip()
        location = request.form.get('location', '').strip()
        end_date_str = request.form.get('end_date', '').strip()

        # Required validation
        required = {
            'title': title,
            'category': category,
            'city': city,
            'date': date_str,
            'time': time_str,
            'description': description,
        }

        for field, value in required.items():
            if not value:
                errors[field] = f"{field.capitalize()} is required."

        # Date parsing
        parsed_date = None
        parsed_end_date = None

        if date_str and 'date' not in errors:
            try:
                parsed_date = datetime.strptime(
                    date_str,
                    '%Y-%m-%d'
                ).date()

            except ValueError:
                errors['date'] = 'Invalid date format (use YYYY-MM-DD).'

        if end_date_str:
            try:
                parsed_end_date = datetime.strptime(
                    end_date_str,
                    '%Y-%m-%d'
                ).date()

            except ValueError:
                errors['end_date'] = 'Invalid end date format (use YYYY-MM-DD).'

        # Save event
        if not errors:

            event = Event(
                title=title,
                category=category,
                city=city,
                start_date=parsed_date,
                time=time_str,
                description=description,
                image=image or None,
                official_link=official_link or None,
                source='admin',
                price=price or None,
                location=location or None,
                end_date=parsed_end_date,
            )

            db.session.add(event)
            db.session.commit()

            flash('Event added successfully.')

            return redirect(url_for('admin.admin_dashboard'))

    return render_template(
        'admin/add_event.html',
        errors=errors,
        form_data=request.form if request.method == 'POST' else {}
    )
# ─── Edit Event ───────────────────────────────────────────────────────────────
@admin_bp.route('/admin/edit-event/<int:event_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_event(event_id):
    event = Event.query.get_or_404(event_id)
    errors = {}

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        category = request.form.get('category', '').strip()
        city = "Riyadh"
        start_date_str = request.form.get('start_date', '').strip()
        time_str = request.form.get('time', '').strip()
        description = request.form.get('description', '').strip()
        official_link = request.form.get('official_link', '').strip()
        price = request.form.get('price', '').strip()
        location = request.form.get('location', '').strip()
        end_date_str = request.form.get('end_date', '').strip()

        # Keep old image by default
        image = event.image

        # Handle uploaded image if admin selects a new one
        image_file = request.files.get('image')

        if image_file and image_file.filename != "":
            filename = secure_filename(image_file.filename)

            upload_folder = os.path.join(
                current_app.root_path,
                "static",
                "uploads"
            )

            os.makedirs(upload_folder, exist_ok=True)

            image_path = os.path.join(upload_folder, filename)
            image_file.save(image_path)

            image = f"/static/uploads/{filename}"

        required = {
            'title': title,
            'category': category,
            'start_date': start_date_str,
            'time': time_str,
            'description': description,
        }

        for field, value in required.items():
            if not value:
                errors[field] = f'{field.replace("_", " ").capitalize()} is required.'

        parsed_start_date = None
        parsed_end_date = None

        if start_date_str and 'start_date' not in errors:
            try:
                parsed_start_date = datetime.strptime(
                    start_date_str,
                    '%Y-%m-%d'
                ).date()
            except ValueError:
                errors['start_date'] = 'Invalid start date format (use YYYY-MM-DD).'

        if end_date_str:
            try:
                parsed_end_date = datetime.strptime(
                    end_date_str,
                    '%Y-%m-%d'
                ).date()
            except ValueError:
                errors['end_date'] = 'Invalid end date format (use YYYY-MM-DD).'

        if not errors:
            event.title = title
            event.category = category
            event.city = city
            event.start_date = parsed_start_date
            event.time = time_str
            event.description = description
            event.image = image
            event.official_link = official_link or None
            event.price = price or None
            event.location = location or None
            event.end_date = parsed_end_date

            db.session.commit()
            flash('Event updated successfully.')
            return redirect(url_for('admin.admin_dashboard'))

    return render_template('admin/edit_event.html', event=event, errors=errors)
# ─── Delete Event ─────────────────────────────────────────────────────────────

@admin_bp.route('/admin/delete-event/<int:event_id>', methods=['POST'])
@login_required
@admin_required
def delete_event(event_id):
    event = Event.query.get_or_404(event_id)

    # Soft delete: the event and all of its historical interaction data
    # (Ratings, Reviews, Favorites, CalendarEvent, RecentlyViewed rows)
    # are preserved. Sprint 4's recommendation system will later use this
    # history. User-facing queries filter on is_active instead.
    event.is_active = False
    db.session.commit()

    flash('Event deleted successfully.')
    return redirect(url_for('admin.admin_dashboard'))
