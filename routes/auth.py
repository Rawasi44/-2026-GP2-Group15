import random
import re
import time
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_mail import Message
from flask_login import login_user, login_required, logout_user, current_user
from models.user import User
from extensions import db, mail

auth_bp = Blueprint('auth', __name__)

CODE_EXPIRY_SECONDS = 10 * 60

# Session keys used to track a pending signup-email verification.
SIGNUP_VERIFY_SESSION_KEYS = (
    'signup_verify_user_id',
    'signup_verify_code',
    'signup_verify_expires_at',
)

PASSWORD_MIN_LENGTH = 8


def generate_verification_code():
    return str(random.randint(1000, 9999))


def code_is_expired(expires_at):
    return not expires_at or time.time() > float(expires_at)


def send_verification_code(recipient_email, code, purpose='password reset'):
    message = Message(
        subject='AFAQ Verification Code',
        recipients=[recipient_email],
        body=(
            f'Your AFAQ verification code is: {code}\n\n'
            f'This code is for {purpose}. It will expire in 10 minutes.\n\n'
            'If you did not request this code, you can safely ignore this email.'
        )
    )

    try:
        mail.send(message)
        print(f"EMAIL SENT SUCCESSFULLY TO: {recipient_email}")
    except Exception as e:
        print("MAIL ERROR:", repr(e))
        raise


def validate_password(password):
    """
    Applies AFAQ's password policy. Returns a list of human-readable error
    messages; an empty list means the password satisfies the policy.

    Policy (applied consistently to registration, password reset, and
    profile password change):
      - at least 8 characters
      - at least one uppercase letter
      - at least one lowercase letter
      - at least one number
      - at least one special character
    """
    errors = []

    if len(password) < PASSWORD_MIN_LENGTH:
        errors.append(f'Password must be at least {PASSWORD_MIN_LENGTH} characters long.')
    if not re.search(r'[A-Z]', password):
        errors.append('Password must contain at least one uppercase letter.')
    if not re.search(r'[a-z]', password):
        errors.append('Password must contain at least one lowercase letter.')
    if not re.search(r'[0-9]', password):
        errors.append('Password must contain at least one number.')
    if not re.search(r'[^A-Za-z0-9]', password):
        errors.append('Password must contain at least one special character.')

    return errors


def start_signup_verification(user):
    """Generates a fresh verification code for the given user, stores it in
    the session, and emails it. Reuses the same code/email helpers as the
    password-reset flow."""
    code = generate_verification_code()
    session['signup_verify_user_id'] = user.user_id
    session['signup_verify_code'] = code
    session['signup_verify_expires_at'] = time.time() + CODE_EXPIRY_SECONDS

    send_verification_code(
        user.email,
        code,
        purpose='verifying your AFAQ account'
    )


def clear_signup_verification_session():
    for key in SIGNUP_VERIFY_SESSION_KEYS:
        session.pop(key, None)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            # Admin accounts are provisioned directly (not through public
            # signup) and are not subject to email verification.
            if user.role != 'admin' and not user.is_verified:
                try:
                    start_signup_verification(user)
                    flash('Please verify your email to activate your account. A new verification code has been sent.')
                except Exception:
                    flash('Please verify your email to activate your account. We could not send a new code — try again shortly.')
                return redirect(url_for('auth.verify_signup_code'))

            login_user(user)

            if user.role == "admin":
                return redirect(url_for('admin.admin_dashboard'))
            return redirect(url_for('events.home'))

        flash('Invalid email or password')

    return render_template('login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('events.home'))


@auth_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        fullname = request.form.get('fullname', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '').strip()

        errors = []

        if not fullname:
            errors.append('Full name is required.')
        if not email:
            errors.append('Email is required.')
        if not password:
            errors.append('Password is required.')
        elif not errors:
            errors.extend(validate_password(password))

        if not errors:
            if User.query.filter_by(fullname=fullname).first():
                errors.append('This username is already taken.')

            if User.query.filter_by(email=email).first():
                errors.append('This email address is already registered.')

        if errors:
            return render_template(
                'signup.html',
                errors=errors,
                fullname=fullname,
                email=email,
            )

        new_user = User(
            fullname=fullname,
            email=email,
            role='user',
            is_verified=False
        )
        new_user.set_password(password)

        try:
            db.session.add(new_user)
            db.session.commit()
        except Exception:
            db.session.rollback()
            return render_template(
                'signup.html',
                errors=['An unexpected error occurred. Please try again.'],
                fullname=fullname,
                email=email,
            )

        # Account is created but inactive until the emailed code is verified.
        try:
            start_signup_verification(new_user)
        except Exception:
            flash(
                'Account created, but the verification email could not be sent. '
                'Please try resending the code.'
            )
            return redirect(url_for('auth.verify_signup_code'))

        flash(
            'A verification code has been sent to your email. '
            'Please verify it to activate your account.'
        )
        return redirect(url_for('auth.verify_signup_code'))

    return render_template(
        'signup.html',
        errors=[],
        fullname='',
        email=''
    )


@auth_bp.route('/signup/verify', methods=['GET', 'POST'])
def verify_signup_code():
    if current_user.is_authenticated:
        return redirect(url_for('events.home'))

    if not session.get('signup_verify_code') or not session.get('signup_verify_user_id'):
        flash('Please sign up first.')
        return redirect(url_for('auth.signup'))

    errors = []

    if request.method == 'POST':
        entered_code = request.form.get('code', '').strip()

        if code_is_expired(session.get('signup_verify_expires_at')):
            errors.append(
                'Verification code expired. Please request a new code.'
            )
        elif entered_code != session.get('signup_verify_code'):
            errors.append('Invalid verification code.')

        if errors:
            return render_template(
                'verify_signup_code.html',
                errors=errors
            )

        user = User.query.get(
            session.get('signup_verify_user_id')
        )

        if not user:
            clear_signup_verification_session()
            flash('Account not found. Please sign up again.')
            return redirect(url_for('auth.signup'))

        user.is_verified = True

        try:
            db.session.commit()
            login_user(user)
        except Exception:
            db.session.rollback()
            return render_template(
                'verify_signup_code.html',
                errors=[
                    'An unexpected error occurred. Please try again.'
                ]
            )

        clear_signup_verification_session()

        flash(
            'Your email has been verified. Welcome to AFAQ!'
        )
        return redirect(url_for('events.home'))

    return render_template(
        'verify_signup_code.html',
        errors=[]
    )


@auth_bp.route('/signup/resend', methods=['POST'])
def resend_signup_code():
    if not session.get('signup_verify_user_id'):
        flash('Please sign up first.')
        return redirect(url_for('auth.signup'))

    user = User.query.get(
        session.get('signup_verify_user_id')
    )

    if not user:
        clear_signup_verification_session()
        flash('Account not found. Please sign up again.')
        return redirect(url_for('auth.signup'))

    try:
        start_signup_verification(user)
        flash(
            'A new verification code has been sent to your email.'
        )
    except Exception:
        flash(
            'Could not send the verification email. '
            'Please check the mail settings and try again.'
        )

    return redirect(url_for('auth.verify_signup_code'))


@auth_bp.route('/user/profile', methods=['GET', 'POST'])
@login_required
def user_profile():
    if current_user.role == 'admin':
        return redirect(url_for('admin.profile'))

    if request.method == 'POST':
        fullname = request.form.get(
            'fullname', ''
        ).strip()
        current_password = request.form.get(
            'current_password', ''
        )
        new_password = request.form.get(
            'new_password', ''
        )
        confirm_password = request.form.get(
            'confirm_password', ''
        )

        errors = []

        if not fullname:
            errors.append('Full name is required.')
        else:
            existing_user = User.query.filter(
                User.fullname == fullname,
                User.user_id != current_user.user_id
            ).first()

            if existing_user:
                errors.append(
                    'This username is already taken.'
                )

        wants_password_change = bool(
            current_password
            or new_password
            or confirm_password
        )

        if wants_password_change:
            if not current_password:
                errors.append(
                    'Current password is required to change your password.'
                )
            elif not current_user.check_password(
                current_password
            ):
                errors.append(
                    'Current password is incorrect.'
                )

            if not new_password:
                errors.append(
                    'New password is required.'
                )
            else:
                errors.extend(
                    validate_password(new_password)
                )

            if new_password != confirm_password:
                errors.append(
                    'New password and confirmation do not match.'
                )

        if errors:
            return render_template(
                'user_profile.html',
                errors=errors
            )

        if wants_password_change:
            code = generate_verification_code()

            session['profile_password_code'] = code
            session['profile_password_expires_at'] = (
                time.time() + CODE_EXPIRY_SECONDS
            )
            session['profile_pending_fullname'] = fullname
            session['profile_pending_new_password'] = (
                new_password
            )

            try:
                send_verification_code(
                    current_user.email,
                    code,
                    purpose='changing your AFAQ password'
                )
            except Exception:
                return render_template(
                    'user_profile.html',
                    errors=[
                        'Could not send the verification email. '
                        'Please check the mail settings and try again.'
                    ]
                )

            flash(
                'A verification code has been sent to your email.'
            )

            return redirect(
                url_for(
                    'auth.verify_profile_password_code'
                )
            )

        current_user.fullname = fullname

        try:
            db.session.commit()
            flash(
                'Profile updated successfully.'
            )
        except Exception:
            db.session.rollback()
            return render_template(
                'user_profile.html',
                errors=[
                    'An unexpected error occurred. '
                    'Please try again.'
                ]
            )

        return redirect(
            url_for('auth.user_profile')
        )

    return render_template(
        'user_profile.html',
        errors=[]
    )


@auth_bp.route(
    '/user/profile/verify-password',
    methods=['GET', 'POST']
)
@login_required
def verify_profile_password_code():
    if current_user.role == 'admin':
        return redirect(url_for('admin.profile'))

    if not session.get('profile_password_code'):
        flash(
            'No password change request was found.'
        )
        return redirect(
            url_for('auth.user_profile')
        )

    errors = []

    if request.method == 'POST':
        entered_code = request.form.get(
            'code', ''
        ).strip()

        if code_is_expired(
            session.get(
                'profile_password_expires_at'
            )
        ):
            errors.append(
                'Verification code expired. '
                'Please request a new code.'
            )
        elif entered_code != session.get(
            'profile_password_code'
        ):
            errors.append(
                'Invalid verification code.'
            )

        pending_fullname = session.get(
            'profile_pending_fullname'
        )
        pending_new_password = session.get(
            'profile_pending_new_password'
        )

        if not pending_new_password:
            errors.append(
                'Password update session expired. '
                'Please try again from your profile page.'
            )

        if errors:
            return render_template(
                'verify_profile_password_code.html',
                errors=errors
            )

        user = User.query.get(
            current_user.user_id
        )

        if not user:
            flash(
                'User account not found.'
            )
            return redirect(
                url_for('auth.login')
            )

        user.fullname = (
            pending_fullname or user.fullname
        )
        user.set_password(
            pending_new_password
        )

        try:
            db.session.commit()
            login_user(user)
        except Exception:
            db.session.rollback()
            return render_template(
                'verify_profile_password_code.html',
                errors=[
                    'An unexpected error occurred. '
                    'Please try again.'
                ]
            )

        session.pop(
            'profile_password_code',
            None
        )
        session.pop(
            'profile_password_expires_at',
            None
        )
        session.pop(
            'profile_pending_fullname',
            None
        )
        session.pop(
            'profile_pending_new_password',
            None
        )

        flash(
            'Password updated successfully.'
        )
        return redirect(
            url_for('auth.user_profile')
        )

    return render_template(
        'verify_profile_password_code.html',
        errors=[]
    )


@auth_bp.route(
    '/forgot-password',
    methods=['GET', 'POST']
)
def forgot_password():
    if current_user.is_authenticated:
        return redirect(
            url_for('auth.user_profile')
        )

    errors = []

    if request.method == 'POST':
        email = request.form.get(
            'email', ''
        ).strip().lower()

        if not email:
            errors.append(
                'Email is required.'
            )
            return render_template(
                'forgot_password.html',
                errors=errors,
                email=email
            )

        user = User.query.filter_by(
            email=email
        ).first()

        if not user:
            errors.append(
                'No account was found with this email.'
            )
            return render_template(
                'forgot_password.html',
                errors=errors,
                email=email
            )

        code = generate_verification_code()

        session['reset_password_user_id'] = (
            user.user_id
        )
        session['reset_password_code'] = code
        session['reset_password_expires_at'] = (
            time.time() + CODE_EXPIRY_SECONDS
        )

        try:
            send_verification_code(
                user.email,
                code,
                purpose='resetting your AFAQ password'
            )
        except Exception:
            return render_template(
                'forgot_password.html',
                errors=[
                    'Could not send the verification email. '
                    'Please check the mail settings and try again.'
                ],
                email=email
            )

        flash(
            'A verification code has been sent to your email.'
        )

        return redirect(
            url_for(
                'auth.verify_reset_password_code'
            )
        )

    return render_template(
        'forgot_password.html',
        errors=[],
        email=''
    )


@auth_bp.route(
    '/reset-password/verify',
    methods=['GET', 'POST']
)
def verify_reset_password_code():
    if current_user.is_authenticated:
        return redirect(
            url_for('auth.user_profile')
        )

    if not session.get(
        'reset_password_code'
    ) or not session.get(
        'reset_password_user_id'
    ):
        flash(
            'Please enter your email first.'
        )
        return redirect(
            url_for('auth.forgot_password')
        )

    errors = []

    if request.method == 'POST':
        entered_code = request.form.get(
            'code', ''
        ).strip()

        new_password = request.form.get(
            'new_password', ''
        )

        confirm_password = request.form.get(
            'confirm_password', ''
        )

        if code_is_expired(
            session.get(
                'reset_password_expires_at'
            )
        ):
            errors.append(
                'Verification code expired. '
                'Please request a new code.'
            )
        elif entered_code != session.get(
            'reset_password_code'
        ):
            errors.append(
                'Invalid verification code.'
            )

        if not new_password:
            errors.append(
                'New password is required.'
            )
        else:
            errors.extend(
                validate_password(
                    new_password
                )
            )

        if new_password != confirm_password:
            errors.append(
                'New password and confirmation do not match.'
            )

        if errors:
            return render_template(
                'verify_reset_code.html',
                errors=errors
            )

        user = User.query.get(
            session.get(
                'reset_password_user_id'
            )
        )

        if not user:
            session.pop(
                'reset_password_user_id',
                None
            )
            session.pop(
                'reset_password_code',
                None
            )
            session.pop(
                'reset_password_expires_at',
                None
            )

            flash(
                'Account not found. Please try again.'
            )

            return redirect(
                url_for('auth.forgot_password')
            )

        user.set_password(
            new_password
        )

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

            return render_template(
                'verify_reset_code.html',
                errors=[
                    'An unexpected error occurred. '
                    'Please try again.'
                ]
            )

        session.pop(
            'reset_password_user_id',
            None
        )
        session.pop(
            'reset_password_code',
            None
        )
        session.pop(
            'reset_password_expires_at',
            None
        )

        flash(
            'Password reset successfully. '
            'Please log in with your new password.'
        )

        return redirect(
            url_for('auth.login')
        )

    return render_template(
        'verify_reset_code.html',
        errors=[]
    )