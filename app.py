from datetime import datetime, timedelta
from functools import wraps
import math
import os
import random
import secrets
import smtplib
from email.message import EmailMessage

from dotenv import load_dotenv
from flask import Flask, jsonify, request, session, redirect, url_for, render_template, flash
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv(
    "SECRET_KEY", "change-this-secret-key-in-production"
)

# Use Neon PostgreSQL on Render, or SQLite for local development.
database_url = os.getenv("DATABASE_URL", "sqlite:///carshare.db")

# Use the psycopg 3 driver installed in requirements.txt.
if database_url.startswith("postgres://"):
    database_url = database_url.replace(
        "postgres://", "postgresql+psycopg://", 1
    )
elif database_url.startswith("postgresql://"):
    database_url = database_url.replace(
        "postgresql://", "postgresql+psycopg://", 1
    )

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
# Check connections before reusing them and
# recycle older connections after five minutes.
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER", "smtp.gmail.com")
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", "587"))
app.config["MAIL_USE_TLS"] = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME", "theschoolrun2026@gmail.com")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD", "")
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER", app.config["MAIL_USERNAME"])
app.config["EMAIL_VERIFICATION_MAX_AGE"] = int(os.getenv("EMAIL_VERIFICATION_MAX_AGE", "86400"))
app.config["PASSWORD_RESET_MAX_AGE"] = int(os.getenv("PASSWORD_RESET_MAX_AGE", "3600"))
app.config["PUBLIC_BASE_URL"] = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

db = SQLAlchemy(app)

def pretty_time(value):
    return value.strftime("%I:%M %p").lstrip("0")


def time_options():
    """Common start-time suggestions in 10-minute intervals. Manual times are also accepted."""
    options = []
    for minutes in range(7 * 60, 21 * 60 + 31, 10):
        hour24, minute = divmod(minutes, 60)
        suffix = "am" if hour24 < 12 else "pm"
        hour12 = hour24 % 12 or 12
        label = f"{hour12}:{minute:02d} {suffix}"
        value = f"{hour24:02d}:{minute:02d}"
        options.append((value, label))
    return options

@app.context_processor
def inject_helpers():
    return {"timedelta": timedelta, "time_options": time_options(), "pretty_time": pretty_time, "journey_passengers": journey_passengers, "calendar_journeys": calendar_journeys, "weekly_time_slots": weekly_time_slots}

pool_members = db.Table(
    "pool_members",
    db.Column("pool_id", db.Integer, db.ForeignKey("pool.id"), primary_key=True),
    db.Column("user_id", db.Integer, db.ForeignKey("user.id"), primary_key=True),
)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    location_name = db.Column(db.String(255))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    completed_shares = db.Column(db.Integer, default=0, nullable=False)
    terms_accepted_at = db.Column(db.DateTime)
    email_verified_at = db.Column(db.DateTime)
    share_passenger_preference = db.Column(db.String(20), default="children", nullable=False)
    child_assignment_preference = db.Column(db.String(20), default="later", nullable=False)
    named_share_space = db.Column(db.Boolean, default=False, nullable=False)
    driver_only_availability = db.Column(db.Boolean, default=False, nullable=False)
    named_passenger_child_ids = db.Column(db.String(1000), default="", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    pools = db.relationship("Pool", secondary=pool_members, back_populates="members")
    children = db.relationship("Child", back_populates="parent", cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Child(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    year_group = db.Column(db.String(50))
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    parent = db.relationship("User", back_populates="children")
    bookings = db.relationship("ShareBooking", back_populates="child")


class Pool(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    invite_code = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    allow_delegated_availability = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    creator = db.relationship("User", foreign_keys=[created_by_id])
    members = db.relationship("User", secondary=pool_members, back_populates="pools")
    availability = db.relationship("Availability", back_populates="pool", cascade="all, delete-orphan")


class Availability(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pool_id = db.Column(db.Integer, db.ForeignKey("pool.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    seats_available = db.Column(db.Integer, nullable=False, default=0)
    is_driver = db.Column(db.Boolean, default=True, nullable=False)
    driver_mode = db.Column(db.String(20), default="confirmed", nullable=False)
    driver_intent = db.Column(db.String(20), default="confirmed", nullable=False)
    pickup_location = db.Column(db.String(255), nullable=True)

    user = db.relationship("User")
    pool = db.relationship("Pool", back_populates="availability")


class ShareBooking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pool_id = db.Column(db.Integer, db.ForeignKey("pool.id"), nullable=False)
    driver_availability_id = db.Column(db.Integer, db.ForeignKey("availability.id"), nullable=False)
    rider_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    child_id = db.Column(db.Integer, db.ForeignKey("child.id"), nullable=True)
    requested_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(30), default="confirmed", nullable=False)

    driver_availability = db.relationship("Availability")
    rider = db.relationship("User")
    child = db.relationship("Child", back_populates="bookings")


class RandomizedSpot(db.Model):
    """A parent/child waiting for the app to match them to another driver's slot."""
    id = db.Column(db.Integer, primary_key=True)
    pool_id = db.Column(db.Integer, db.ForeignKey("pool.id"), nullable=False)
    randomized_availability_id = db.Column(db.Integer, db.ForeignKey("availability.id"), nullable=False)
    rider_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    child_id = db.Column(db.Integer, db.ForeignKey("child.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    randomized_availability = db.relationship("Availability")
    rider = db.relationship("User")
    child = db.relationship("Child")


# -----------------------------
# Helpers
# -----------------------------

def email_verification_serializer():
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="the-school-run-email-verification")


def make_email_verification_token(user):
    return email_verification_serializer().dumps({"user_id": user.id, "email": user.email})


def verify_email_token(token):
    try:
        data = email_verification_serializer().loads(token, max_age=app.config["EMAIL_VERIFICATION_MAX_AGE"])
    except (BadSignature, SignatureExpired):
        return None
    user = db.session.get(User, data.get("user_id"))
    if not user or user.email != data.get("email"):
        return None
    return user


def make_password_reset_token(user):
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="the-school-run-password-reset").dumps({"user_id": user.id, "email": user.email, "password_hash": user.password_hash})


def verify_password_reset_token(token):
    try:
        data = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="the-school-run-password-reset").loads(
            token, max_age=app.config["PASSWORD_RESET_MAX_AGE"]
        )
    except (BadSignature, SignatureExpired):
        return None
    user = db.session.get(User, data.get("user_id"))
    if not user or user.email != data.get("email") or user.password_hash != data.get("password_hash"):
        return None
    return user


def absolute_app_url(endpoint, **values):
    if app.config["PUBLIC_BASE_URL"]:
        return app.config["PUBLIC_BASE_URL"] + url_for(endpoint, **values)
    return url_for(endpoint, _external=True, **values)


def send_verification_email(user):
    if not app.config["MAIL_PASSWORD"]:
        raise RuntimeError("MAIL_PASSWORD is not configured. Add the Gmail App Password to your .env file.")
    token = make_email_verification_token(user)
    verification_link = absolute_app_url("verify_email", token=token)
    message = EmailMessage()
    message["Subject"] = "Verify your email for The School Run"
    message["From"] = app.config["MAIL_DEFAULT_SENDER"]
    message["To"] = user.email
    message.set_content(f"""Hello {user.name},

Welcome to The School Run.

Please verify your email address by opening this link:

{verification_link}

This verification link expires in 24 hours. If you did not create a The School Run account, you can ignore this email.

The School Run
""")
    with smtplib.SMTP(app.config["MAIL_SERVER"], app.config["MAIL_PORT"], timeout=20) as smtp:
        smtp.ehlo()
        if app.config["MAIL_USE_TLS"]:
            smtp.starttls()
            smtp.ehlo()
        smtp.login(app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"].replace(" ", ""))
        smtp.send_message(message)


def send_password_reset_email(user):
    if not app.config["MAIL_PASSWORD"]:
        raise RuntimeError("MAIL_PASSWORD is not configured. Add the Gmail App Password to your .env file.")
    token = make_password_reset_token(user)
    reset_link = absolute_app_url("reset_password", token=token)
    message = EmailMessage()
    message["Subject"] = "Reset your The School Run password"
    message["From"] = app.config["MAIL_DEFAULT_SENDER"]
    message["To"] = user.email
    message.set_content(f"""Hello {user.name},

We received a request to reset your The School Run password.

Reset your password by opening this link:

{reset_link}

This password-reset link expires in 1 hour. If you did not request a password reset, you can ignore this email.

The School Run
""")
    with smtplib.SMTP(app.config["MAIL_SERVER"], app.config["MAIL_PORT"], timeout=20) as smtp:
        smtp.ehlo()
        if app.config["MAIL_USE_TLS"]:
            smtp.starttls()
            smtp.ehlo()
        smtp.login(app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"].replace(" ", ""))
        smtp.send_message(message)


def current_user():
    user_id = session.get("user_id")
    return db.session.get(User, user_id) if user_id else None


def verified_login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("login"))
        if not user.email_verified_at:
            session.clear()
            flash("Please verify your email address before using The School Run.")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def user_in_pool(user, pool):
    return any(member.id == user.id for member in pool.members)


def selected_driver_for(pool, acting_user, driver_id):
    """Return the selected driver if the acting user is allowed to publish for them."""
    if not driver_id:
        return acting_user
    try:
        driver_id = int(driver_id)
    except (TypeError, ValueError):
        return None
    driver = db.session.get(User, driver_id)
    if not driver or not user_in_pool(driver, pool):
        return None
    if driver.id != acting_user.id and not pool.allow_delegated_availability:
        return None
    return driver


def match_randomized_spots(pool, confirmed_availability):
    """Assign waiting randomized passengers to a matching confirmed driver slot."""
    if confirmed_availability.driver_mode != "confirmed":
        return 0
    waiting = (RandomizedSpot.query
               .join(Availability, RandomizedSpot.randomized_availability_id == Availability.id)
               .filter(RandomizedSpot.pool_id == pool.id,
                       Availability.start_time == confirmed_availability.start_time,
                       Availability.end_time == confirmed_availability.end_time).all())
    # Keep siblings together where possible, then prefer passengers whose saved
    # location is closest to the confirmed driver's location.
    sibling_counts = {}
    for spot in waiting:
        sibling_counts[spot.rider_id] = sibling_counts.get(spot.rider_id, 0) + 1
    waiting.sort(key=lambda spot: (
        -sibling_counts.get(spot.rider_id, 1),
        _driver_distance_to_user(confirmed_availability.user, spot.rider),
        spot.created_at,
        spot.id,
    ))
    added = 0
    for spot in waiting:
        if remaining_seats(confirmed_availability) <= 0:
            break
        if spot.rider_id == confirmed_availability.user_id:
            continue
        existing = None
        if spot.child_id:
            # The same child can be booked on different days/times.
            # Only reject a duplicate in the exact target time slot.
            existing = (ShareBooking.query
                        .join(Availability, ShareBooking.driver_availability_id == Availability.id)
                        .filter(ShareBooking.pool_id == pool.id,
                                ShareBooking.child_id == spot.child_id,
                                ShareBooking.status == "confirmed",
                                Availability.start_time == confirmed_availability.start_time,
                                Availability.end_time == confirmed_availability.end_time)
                        .first())
        else:
            existing = ShareBooking.query.filter_by(
                pool_id=pool.id,
                driver_availability_id=confirmed_availability.id,
                rider_id=spot.rider_id,
                child_id=None,
                status="confirmed",
            ).first()
        if existing:
            db.session.delete(spot)
            continue
        db.session.add(ShareBooking(pool_id=pool.id, driver_availability_id=confirmed_availability.id,
                                    rider_id=spot.rider_id, child_id=spot.child_id))
        # If this was the driver's own child from an Available to Drive slot,
        # remove the provisional booking from the flexible slot now that the
        # child has a confirmed driver.
        if spot.child_id:
            provisional = ShareBooking.query.filter_by(
                pool_id=pool.id,
                driver_availability_id=spot.randomized_availability_id,
                rider_id=spot.rider_id,
                child_id=spot.child_id,
                status="confirmed",
            ).first()
            if provisional:
                db.session.delete(provisional)
        confirmed_availability.user.completed_shares += 1
        db.session.delete(spot)
        added += 1
    return added

def create_randomized_spots(pool, availability, rider):
    """Create passenger spots based on the user's saved preference."""
    if availability.driver_mode != "randomized":
        return 0
    existing = RandomizedSpot.query.filter_by(randomized_availability_id=availability.id).count()
    if existing:
        return existing
    added = 0
    if rider.share_passenger_preference == "self":
        db.session.add(RandomizedSpot(pool_id=pool.id, randomized_availability_id=availability.id, rider_id=rider.id))
        added = 1
    else:
        selected_ids = {int(x) for x in (rider.named_passenger_child_ids or "").split(",") if x.isdigit()}
        active_children = [child for child in rider.children if child.active]
        # The driver's own children are already attached to this slot.
        # For an Available to Drive slot, they are also represented as
        # flexible passenger spots so the matching process can move them
        # to the eventual confirmed driver when needed.
        chosen = [child for child in active_children if not rider.named_passenger_child_ids or child.id in selected_ids]
        existing_child_ids = {
            b.child_id for b in own_child_bookings(availability) if b.child_id
        }
        for child in chosen:
            if child.id in existing_child_ids:
                continue
            db.session.add(RandomizedSpot(pool_id=pool.id, randomized_availability_id=availability.id,
                                          rider_id=rider.id, child_id=child.id))
            added += 1
    return added

def matching_driver_options(availability):
    rows = (Availability.query
            .filter(Availability.pool_id == availability.pool_id,
                    Availability.start_time == availability.start_time,
                    Availability.end_time == availability.end_time,
                    Availability.driver_mode == "confirmed",
                    Availability.id != availability.id)
            .order_by(Availability.user_id).all())
    seen = set(); result = []
    for row in rows:
        if row.user_id not in seen:
            seen.add(row.user_id); result.append(row.user)
    return result

def _slot_rows(pool_id, start_time, end_time):
    return (Availability.query
            .filter_by(pool_id=pool_id, start_time=start_time, end_time=end_time)
            .order_by(Availability.id.asc()).all())


def _driver_distance_to_user(driver, rider):
    return haversine_km(driver.latitude, driver.longitude, rider.latitude, rider.longitude)


def confirmed_drive_count(user, pool_id=None):
    """Count confirmed driving slots, scoped to a Pool when supplied."""
    query = Availability.query.filter(Availability.user_id == user.id,
                                      Availability.driver_mode == "confirmed")
    if pool_id is not None:
        query = query.filter(Availability.pool_id == pool_id)
    return query.count()


def _rotation_counts(user, pool_id, exclude_start=None, exclude_end=None):
    """Return confirmed-drive counts for fair rotation in one Pool.

    The slot currently being allocated is excluded. Otherwise a driver can
    gain a confirmed drive, immediately look less fair because of that same
    drive, and then get replaced again on the next reconciliation pass.
    """
    query = (Availability.query
             .filter(Availability.pool_id == pool_id,
                     Availability.user_id == user.id,
                     Availability.driver_mode == "confirmed"))
    if exclude_start is not None and exclude_end is not None:
        query = query.filter(~((Availability.start_time == exclude_start) &
                               (Availability.end_time == exclude_end)))
    rows = query.all()
    days = {}
    times = {}
    for row in rows:
        days[row.start_time.weekday()] = days.get(row.start_time.weekday(), 0) + 1
        key = row.start_time.strftime("%H:%M")
        times[key] = times.get(key, 0) + 1
    return {"total": len(rows), "days": days, "times": times}


def _choose_fair_backup(rows, pool, target_start=None, current_id=None):
    """Choose a flexible driver using stable, fair rotation.

    Counts are calculated *excluding the slot being allocated*. This prevents
    repeated reconciliation passes from making the currently selected driver
    look less fair and then handing the same slot to the next person.

    Priority is: fewer total confirmed drives, then fewer on the same weekday,
    then fewer at the same start time. Ties use a stable pseudo-random order
    for this slot, so the database insertion order (and therefore the last
    person to click Save) cannot decide who drives. If the current driver is
    tied for the fairest position, they are retained, which stops a refresh
    from constantly swapping an already-confirmed run.
    """
    if not rows:
        return None

    target_weekday = target_start.weekday() if target_start else None
    target_time = target_start.strftime("%H:%M") if target_start else None
    scored = []
    for row in rows:
        counts = _rotation_counts(
            row.user, row.pool_id,
            target_start,
            target_start + (row.end_time - row.start_time) if target_start else None,
        )
        same_day = counts["days"].get(target_weekday, 0) if target_weekday is not None else 0
        same_time = counts["times"].get(target_time, 0) if target_time else 0
        # Stable per-slot/user ordering: random-looking, but does not change
        # every time reconcile_slot_drivers() is called.
        import hashlib
        seed = f"{row.pool_id}|{target_start.isoformat() if target_start else ''}|{row.user_id}".encode()
        tie = hashlib.sha256(seed).hexdigest()
        scored.append((row, counts["total"], same_day, same_time, tie))

    min_total = min(item[1] for item in scored)
    fair = [item for item in scored if item[1] == min_total]
    min_day = min(item[2] for item in fair)
    fair = [item for item in fair if item[2] == min_day]
    min_time = min(item[3] for item in fair)
    fair = [item for item in fair if item[3] == min_time]

    # Stable pseudo-random ordering deliberately does not use Availability.id
    # or insertion order. This means adding a new Available-to-Drive parent
    # can change the selected driver when the fairness tier is tied, while
    # repeated refresh/reconcile calls keep the same result instead of
    # oscillating between parents.
    fair.sort(key=lambda item: item[4])
    return fair[0][0]


def _remove_randomized_spots_for_child(pool_id, child_id, start_time, end_time):
    spots = (RandomizedSpot.query
             .join(Availability, RandomizedSpot.randomized_availability_id == Availability.id)
             .filter(RandomizedSpot.pool_id == pool_id,
                     RandomizedSpot.child_id == child_id,
                     Availability.start_time == start_time,
                     Availability.end_time == end_time).all())
    for spot in spots:
        db.session.delete(spot)


def _ensure_confirmed_driver_children(availability):
    """Confirmed drivers always travel with their active children.

    If a driver's child was already booked on another confirmed car for the
    same exact slot, move that child to the driver's own confirmed car.
    """
    driver = availability.user
    active_children = [c for c in driver.children if c.active]
    for child in active_children:
        existing = (ShareBooking.query
                    .join(Availability, ShareBooking.driver_availability_id == Availability.id)
                    .filter(ShareBooking.pool_id == availability.pool_id,
                            ShareBooking.child_id == child.id,
                            ShareBooking.status == "confirmed",
                            Availability.start_time == availability.start_time,
                            Availability.end_time == availability.end_time)
                    .first())
        if existing and existing.driver_availability_id != availability.id:
            db.session.delete(existing)
        if not existing or existing.driver_availability_id != availability.id:
            db.session.add(ShareBooking(pool_id=availability.pool_id,
                                        driver_availability_id=availability.id,
                                        rider_id=driver.id,
                                        child_id=child.id,
                                        status="confirmed"))
        _remove_randomized_spots_for_child(availability.pool_id, child.id,
                                           availability.start_time, availability.end_time)


def _confirmed_external_bookings(availability):
    return (ShareBooking.query
            .filter_by(driver_availability_id=availability.id, status="confirmed")
            .filter(ShareBooking.rider_id != availability.user_id)
            .all())


def _promote_backup_for_slot(pool_id, start_time, end_time):
    """Promote the fairest Available-to-Drive offer to a confirmed driver."""
    rows = _slot_rows(pool_id, start_time, end_time)
    flexible = [r for r in rows if (r.driver_intent or r.driver_mode) == "randomized"
                and r.driver_mode != "confirmed"]
    if not flexible:
        return None
    pool = db.session.get(Pool, pool_id)
    chosen = _choose_fair_backup(flexible, pool, start_time)
    if not chosen:
        return None
    chosen.driver_mode = "confirmed"
    _ensure_confirmed_driver_children(chosen)
    return chosen


def _demote_driver_children_to_waiting(row):
    """When a flexible driver is not selected, move their own-child bookings to waiting spots."""
    own = own_child_bookings(row)
    for booking in own:
        if not booking.child_id:
            continue
        existing = RandomizedSpot.query.filter_by(
            pool_id=row.pool_id, randomized_availability_id=row.id,
            rider_id=row.user_id, child_id=booking.child_id).first()
        if not existing:
            db.session.add(RandomizedSpot(pool_id=row.pool_id, randomized_availability_id=row.id,
                                          rider_id=row.user_id, child_id=booking.child_id))
        db.session.delete(booking)


def reconcile_slot_drivers(pool_id, start_time, end_time):
    """Make one exact Pool/date/time slot self-consistent and rotate fairly.

    Driving Definitely offers always stay confirmed. When everyone is
    Available to Drive, the confirmed driver is recalculated whenever the slot
    changes, using the parent's number of confirmed driving slots. This means a
    parent with fewer confirmed drives can replace the current flexible driver
    rather than the first parent staying confirmed forever.

    If the newly selected driver cannot accommodate passengers already booked,
    the previous driver is retained as an additional confirmed car so no booked
    passenger is displaced.
    """
    rows = _slot_rows(pool_id, start_time, end_time)
    if not rows:
        return []
    pool = rows[0].pool
    definite = [r for r in rows if (r.driver_intent or r.driver_mode) == "confirmed"]
    flexible = [r for r in rows if (r.driver_intent or r.driver_mode) == "randomized"]
    current_flexible = [r for r in flexible if r.driver_mode == "confirmed"]

    if definite:
        confirmed = definite[:]
    else:
        # Never let database insertion order decide the confirmed driver.
        # Recalculate the fairest driver every time availability changes.
        current_flexible_row = current_flexible[0] if current_flexible else None
        # Include the current flexible driver as a candidate so a fair run is
        # kept stable unless a genuinely less-used parent has joined.
        chosen = _choose_fair_backup(flexible, pool, start_time, current_id=current_flexible_row.id if current_flexible_row else None)
        confirmed = [chosen] if chosen else []

    confirmed_ids = {r.id for r in confirmed}
    for row in rows:
        if row.id not in confirmed_ids:
            _demote_driver_children_to_waiting(row)
        row.driver_mode = "confirmed" if row.id in confirmed_ids else "randomized"

    for driver in confirmed:
        _ensure_confirmed_driver_children(driver)

    # Move booked passengers from a driver who has just been rotated out.
    # Prefer an already-confirmed car with space. If that is not possible,
    # promote the fairest remaining backup so booked passengers are protected.
    for row in rows:
        if row.id in confirmed_ids:
            continue
        for booking in _confirmed_external_bookings(row):
            target = next((d for d in sorted(confirmed, key=lambda x: (confirmed_drive_count(x.user, x.pool_id), x.id))
                           if remaining_seats(d) > 0), None)
            if not target:
                candidates = [r for r in flexible if r.id not in confirmed_ids]
                target = _choose_fair_backup(candidates, pool, start_time)
                if target:
                    target.driver_mode = "confirmed"
                    confirmed.append(target)
                    confirmed_ids.add(target.id)
                    _ensure_confirmed_driver_children(target)
                    _demote_driver_children_to_waiting(row)
            if target and target.id != row.id and remaining_seats(target) > 0:
                old_name = row.user.name
                booking.driver_availability_id = target.id
                if booking.rider and booking.rider_id != target.user_id:
                    send_lift_reassignment_notification(booking.rider, old_name, target.user.name,
                                                        target.start_time, target.end_time, target.pickup_location)

    # Recreate waiting passenger spots for every non-confirmed flexible offer.
    for row in rows:
        if row.id not in confirmed_ids:
            create_randomized_spots(pool, row, row.user)

    return sorted(confirmed, key=lambda a: (confirmed_drive_count(a.user, a.pool_id), a.id))


def rebalance_slot(pool_id, start_time, end_time):
    """Repeatedly fill named passenger requests and promote backup drivers only when needed."""
    pool = db.session.get(Pool, pool_id)
    if not pool:
        return []
    for _ in range(20):
        confirmed = reconcile_slot_drivers(pool_id, start_time, end_time)
        for driver in confirmed:
            match_randomized_spots(pool, driver)
        pending = (RandomizedSpot.query
                   .join(Availability, RandomizedSpot.randomized_availability_id == Availability.id)
                   .filter(RandomizedSpot.pool_id == pool_id,
                           Availability.start_time == start_time,
                           Availability.end_time == end_time).count())
        confirmed = [r for r in _slot_rows(pool_id, start_time, end_time) if r.driver_mode == "confirmed"]
        spare = sum(remaining_seats(r) for r in confirmed)
        if pending <= spare:
            return confirmed
        promoted = _promote_backup_for_slot(pool_id, start_time, end_time)
        if not promoted:
            return confirmed
    return [r for r in _slot_rows(pool_id, start_time, end_time) if r.driver_mode == "confirmed"]


# Backwards-compatible name used by older routes.
def normalize_slot_drivers(pool_id, start_time, end_time):
    confirmed = rebalance_slot(pool_id, start_time, end_time)
    return confirmed[0] if confirmed else None


def finish_30_minutes(start_time):
    return start_time + timedelta(minutes=30)


def send_lift_change_notification(recipient, driver_name, old_start, old_end, new_start, new_end, pickup_location=None):
    """Alert a booked parent when their lift changes and no other confirmed driver is available."""
    if not app.config["MAIL_PASSWORD"]:
        return False
    message = EmailMessage()
    message["Subject"] = "Change to your booked lift – The School Run"
    message["From"] = app.config["MAIL_DEFAULT_SENDER"]
    message["To"] = recipient.email
    old_when = f"{old_start.strftime('%a %d %b, ')}{pretty_time(old_start)}–{pretty_time(old_end)}"
    new_when = f"{new_start.strftime('%a %d %b, ')}{pretty_time(new_start)}–{pretty_time(new_end)}"
    pickup_line = f"\nPickup location: {pickup_location}" if pickup_location else ""
    message.set_content(f"""Hello {recipient.name},

There has been a change to a lift you have already booked in The School Run.

Driver: {driver_name}
Previous journey: {old_when}
New journey: {new_when}{pickup_line}

At the moment, no other confirmed driver is assigned to the original journey time. Please check The School Run and make alternative arrangements if needed.

The School Run
""")
    try:
        with smtplib.SMTP(app.config["MAIL_SERVER"], app.config["MAIL_PORT"], timeout=20) as smtp:
            smtp.ehlo()
            if app.config["MAIL_USE_TLS"]:
                smtp.starttls(); smtp.ehlo()
            smtp.login(app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"].replace(" ", ""))
            smtp.send_message(message)
        return True
    except Exception:
        app.logger.exception("Could not send lift-change notification to %s", recipient.email)
        return False


def send_lift_reassignment_notification(recipient, old_driver, new_driver, start_time, end_time, pickup_location=None):
    """Tell a passenger parent that their booked driver has changed."""
    if not app.config["MAIL_PASSWORD"]:
        return False
    message = EmailMessage()
    message["Subject"] = "Your school-run driver has changed – The School Run"
    message["From"] = app.config["MAIL_DEFAULT_SENDER"]
    message["To"] = recipient.email
    when = f"{start_time.strftime('%a %d %b, ')}{pretty_time(start_time)}–{pretty_time(end_time)}"
    pickup_line = f"\nPickup location: {pickup_location}" if pickup_location else ""
    message.set_content(f"""Hello {recipient.name},

Your booked school-run journey has been reassigned in The School Run.

Journey: {when}
Previous driver: {old_driver}
New driver: {new_driver}{pickup_line}

Your booked passenger place has been kept. Please check the app for the latest details.

The School Run
""")
    try:
        with smtplib.SMTP(app.config["MAIL_SERVER"], app.config["MAIL_PORT"], timeout=20) as smtp:
            smtp.ehlo()
            if app.config["MAIL_USE_TLS"]:
                smtp.starttls(); smtp.ehlo()
            smtp.login(app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"].replace(" ", ""))
            smtp.send_message(message)
        return True
    except Exception:
        app.logger.exception("Could not send reassignment notification to %s", recipient.email)
        return False


def send_lift_deleted_notification(recipient, driver_name, start_time, end_time, pickup_location=None):
    """Tell a passenger parent that their booked driver has cancelled."""
    if not app.config["MAIL_PASSWORD"]:
        return False
    message = EmailMessage()
    message["Subject"] = "Your school-run lift was cancelled – The School Run"
    message["From"] = app.config["MAIL_DEFAULT_SENDER"]
    message["To"] = recipient.email
    when = f"{start_time.strftime('%a %d %b, ')}{pretty_time(start_time)}–{pretty_time(end_time)}"
    pickup_line = f"\nPickup location: {pickup_location}" if pickup_location else ""
    message.set_content(f"""Hello {recipient.name},

A school-run lift you had booked has been deleted from The School Run.

Journey: {when}
Previous driver: {driver_name}{pickup_line}

There was no replacement confirmed driver available for the same time. Please check The School Run and make alternative arrangements.

The School Run
""")
    try:
        with smtplib.SMTP(app.config["MAIL_SERVER"], app.config["MAIL_PORT"], timeout=20) as smtp:
            smtp.ehlo()
            if app.config["MAIL_USE_TLS"]:
                smtp.starttls(); smtp.ehlo()
            smtp.login(app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"].replace(" ", ""))
            smtp.send_message(message)
        return True
    except Exception:
        app.logger.exception("Could not send deletion notification to %s", recipient.email)
        return False


def week_monday(value=None):
    if value is None:
        value = datetime.now().date()
    elif isinstance(value, str):
        value = datetime.fromisoformat(value).date()
    return value - timedelta(days=value.weekday())


def calendar_data(user, week_start):
    """Return every confirmed driver journey in the user's Pools for the week.

    The main calendar is a Pool-wide planning view, so it intentionally shows
    confirmed drives offered by all parents in every Pool the user belongs to.
    Passenger bookings are loaded separately by journey_passengers().
    """
    start_dt = datetime.combine(week_start, datetime.min.time())
    end_dt = start_dt + timedelta(days=7)
    pool_ids = [p.id for p in user.pools]
    if not pool_ids:
        return []

    return (Availability.query
            .filter(Availability.pool_id.in_(pool_ids),
                    Availability.driver_mode == "confirmed",
                    Availability.start_time >= start_dt,
                    Availability.start_time < end_dt)
            .order_by(Availability.start_time, Availability.end_time, Availability.pool_id, Availability.id)
            .all())


def calendar_journeys(user, week_start, confirmed_only=False):
    """Group matching offers into one clear calendar slot.

    Offers are grouped by Pool, date, start time and finish time. If two parents
    offer a lift at the same time, the calendar therefore shows one time slot
    containing both drivers, with each driver's confirmed passenger children
    listed underneath them.
    """
    offers = calendar_data(user, week_start)
    if confirmed_only:
        offers = [a for a in offers if a.driver_mode == "confirmed"]

    grouped = {}
    for offer in offers:
        key = (offer.pool_id, offer.start_time, offer.end_time)
        grouped.setdefault(key, []).append(offer)

    journeys = []
    for (pool_id, start_time, end_time), rows in sorted(grouped.items(), key=lambda item: item[0][1:]):
        rows = sorted(rows, key=journey_priority_key)
        journeys.append({
            "pool_id": pool_id,
            "start_time": start_time,
            "end_time": end_time,
            "pool": rows[0].pool,
            "offers": rows,
        })
    return journeys

def weekly_time_slots(availabilities):
    """Return the unique start times used by a weekly timetable."""
    return sorted({a.start_time.strftime("%H:%M") for a in availabilities})

def journey_passengers(availability):
    return (ShareBooking.query
            .filter_by(driver_availability_id=availability.id, status="confirmed")
            .order_by(ShareBooking.rider_id, ShareBooking.child_id)
            .all())

def journey_priority_key(availability):
    # Driving Definitely first. Within flexible offers, parents with fewer
    # completed shared journeys are preferred for fairer rotation.
    return (0 if availability.driver_mode == "confirmed" else 1,
            confirmed_drive_count(availability.user, availability.pool_id),
            availability.start_time, availability.id)

def parse_start_time(value):
    try:
        parsed = datetime.strptime(value.strip(), "%H:%M")
    except (AttributeError, ValueError):
        raise ValueError("Please enter a valid time such as 07:40 or 17:05.")
    if not (7 * 60 <= parsed.hour * 60 + parsed.minute <= 21 * 60 + 30):
        raise ValueError("Please choose a time between 7:00 am and 9:30 pm.")
    return parsed.time()


def already_booked_seats(availability):
    """Count seats used by passengers from other families.

    A driver's own children are attached to the lift so they appear in the
    schedule, but they do not use the Pool's shared passenger-seat allowance.
    The number entered when offering a lift is therefore the number of vacant
    shared spots offered to other parents.
    """
    return ShareBooking.query.filter_by(
        driver_availability_id=availability.id, status="confirmed"
    ).filter(ShareBooking.rider_id != availability.user_id).count()


def own_child_bookings(availability):
    return ShareBooking.query.filter_by(
        driver_availability_id=availability.id,
        rider_id=availability.user_id,
        status="confirmed"
    ).all()


def set_driver_children_for_slot(availability, driver, child_ids=None):
    """Set the driver's own named passengers for a lift.

    Driver children are optional. This lets a parent create availability first
    and add/remove their children later from My offered lifts.
    """
    selected = set()
    if child_ids is not None:
        for value in child_ids:
            try:
                selected.add(int(value))
            except (TypeError, ValueError):
                continue
    valid_ids = {child.id for child in driver.children if child.active}
    selected &= valid_ids
    existing = own_child_bookings(availability)
    existing_by_child = {booking.child_id: booking for booking in existing if booking.child_id}
    for child_id, booking in existing_by_child.items():
        if child_id not in selected:
            db.session.delete(booking)
    added = []
    for child in driver.children:
        if not child.active or child.id not in selected or child.id in existing_by_child:
            continue
        db.session.add(ShareBooking(pool_id=availability.pool_id,
                                    driver_availability_id=availability.id,
                                    rider_id=driver.id, child_id=child.id,
                                    status="confirmed"))
        added.append(child)
    return added


def add_driver_children_to_slot(availability, driver):
    """Backward-compatible helper: older copied/demo lifts keep active children."""
    return set_driver_children_for_slot(availability, driver, [c.id for c in driver.children if c.active])


def remaining_seats(availability):
    return max(0, availability.seats_available - already_booked_seats(availability))


def haversine_km(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return float("inf")
    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def intervals_overlap(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def distance_to_group(user, users):
    located = [u for u in users if u.latitude is not None and u.longitude is not None]
    if not located or user.latitude is None or user.longitude is None:
        return float("inf")
    avg_lat = sum(u.latitude for u in located) / len(located)
    avg_lon = sum(u.longitude for u in located) / len(located)
    return haversine_km(user.latitude, user.longitude, avg_lat, avg_lon)


def select_driver(candidates, group_users):
    if not candidates:
        return None
    counts = {a.user_id: confirmed_drive_count(a.user, a.pool_id) for a in candidates}
    min_count = min(counts.values())
    fair = [a for a in candidates if counts[a.user_id] == min_count]
    distances = [(a, distance_to_group(a.user, group_users)) for a in fair]
    finite = [(a, d) for a, d in distances if math.isfinite(d)]
    if finite:
        finite.sort(key=lambda x: x[1])
        best_distance = finite[0][1]
        tolerance = max(1.0, best_distance * 0.25)
        shortlist = [a for a, d in finite if d <= best_distance + tolerance]
        return random.choice(shortlist)
    return random.choice(fair)


def visible_lifts_for_pool(user, pool):
    """Return confirmed lifts with space, or the fairest backup when a slot is full."""
    rows = (Availability.query.filter_by(pool_id=pool.id)
            .filter(Availability.user_id != user.id)
            .order_by(Availability.start_time, Availability.id).all())
    grouped = {}
    for row in rows:
        grouped.setdefault((row.start_time, row.end_time), []).append(row)
    result = []
    for slot_rows in grouped.values():
        confirmed_with_space = [r for r in slot_rows if r.driver_mode == "confirmed" and remaining_seats(r) > 0]
        if confirmed_with_space:
            result.extend(confirmed_with_space)
        else:
            backups = [r for r in slot_rows if (r.driver_intent or r.driver_mode) == "randomized"]
            if backups:
                chosen = _choose_fair_backup(backups, pool)
                if chosen:
                    result.append(chosen)
    return sorted(result, key=lambda a: (a.start_time, a.id))


def find_matches(pool, rider, start_time, end_time):
    candidates = []
    for availability in pool.availability:
        if not availability.is_driver:
            continue
        if not intervals_overlap(availability.start_time, availability.end_time, start_time, end_time):
            continue
        if availability.user_id == rider.id or remaining_seats(availability) <= 0:
            continue
        distance = haversine_km(rider.latitude, rider.longitude,
                                availability.user.latitude, availability.user.longitude)
        candidates.append({
            "availability": availability,
            "distance_km": distance,
            "completed_shares": availability.user.completed_shares,
            "confirmed_drive_count": confirmed_drive_count(availability.user, availability.pool_id),
            "remaining_seats": remaining_seats(availability),
        })
    candidates.sort(key=lambda item: (0 if item["availability"].driver_mode == "confirmed" else 1, item["confirmed_drive_count"], item["distance_km"], item["availability"].start_time))
    return candidates


# -----------------------------
# Pages
# -----------------------------

@app.route("/")
def index():
    if current_user():
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        if request.form.get("accept_terms") != "1":
            flash("By creating an account, you are confirming that you accept the Terms & Conditions.")
            return redirect(url_for("register"))
        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.")
            return redirect(url_for("register"))
        user = User(name=name, email=email, terms_accepted_at=datetime.utcnow())
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        try:
            send_verification_email(user)
        except Exception:
            app.logger.exception("Unable to send verification email")
            flash("Your account was created, but we could not send the verification email. Please check the email settings and request a new verification email.")
            return redirect(url_for("resend_verification"))
        flash("Account created. Please check your email and click the verification link before logging in.")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            flash("Invalid email or password.")
            return redirect(url_for("login"))
        if not user.email_verified_at:
            flash("Please verify your email address first. We have not logged you in yet.")
            return redirect(url_for("resend_verification"))
        session["user_id"] = user.id
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user:
            try:
                send_password_reset_email(user)
            except Exception:
                app.logger.exception("Unable to send password reset email")
        flash("If an account exists for that email address, a password reset email has been sent. Please check your inbox and spam folder.")
        return redirect(url_for("login"))
    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = verify_password_reset_token(token)
    if not user:
        flash("That password reset link is invalid or has expired. Please request a new one.")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(password) < 8:
            flash("Your new password must be at least 8 characters long.")
            return render_template("reset_password.html")
        if password != confirm:
            flash("The two passwords do not match.")
            return render_template("reset_password.html")
        user.set_password(password)
        db.session.commit()
        flash("Your password has been reset. You can now log in.")
        return redirect(url_for("login"))
    return render_template("reset_password.html")


@app.route("/verify-email/<token>")
def verify_email(token):
    user = verify_email_token(token)
    if not user:
        flash("That verification link is invalid or has expired. Please request a new verification email.")
        return redirect(url_for("resend_verification"))
    if not user.email_verified_at:
        user.email_verified_at = datetime.utcnow()
        db.session.commit()
    session["user_id"] = user.id
    flash("Email verified. Welcome to The School Run!")
    return redirect(url_for("dashboard"))


@app.route("/resend-verification", methods=["GET", "POST"])
def resend_verification():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user and not user.email_verified_at:
            try:
                send_verification_email(user)
            except Exception:
                app.logger.exception("Unable to resend verification email")
        flash("If that email belongs to an unverified The School Run account, a new verification email has been sent. Please check your inbox and spam folder.")
        return redirect(url_for("login"))
    return render_template("resend_verification.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/profile", methods=["GET", "POST"])
@verified_login_required
def profile():
    user = current_user()
    if request.method == "POST":
        user.name = request.form["name"].strip()
        user.location_name = request.form.get("location_name", "").strip()
        db.session.commit()
        flash("Parent profile updated.")
        return redirect(url_for("profile"))
    selected_named_child_ids = {int(x) for x in (user.named_passenger_child_ids or "").split(",") if x.isdigit()}
    return render_template("profile.html", user=user, selected_named_child_ids=selected_named_child_ids)


@app.route("/children/add", methods=["POST"])
@verified_login_required
def add_child():
    user = current_user()
    name = request.form.get("name", "").strip()
    year_group = request.form.get("year_group", "").strip()
    if not name:
        flash("Please enter your child's name.")
    else:
        db.session.add(Child(parent_id=user.id, name=name, year_group=year_group))
        db.session.commit()
        flash(f"{name} was added to your family profile.")
    return redirect(request.referrer or url_for("profile"))


@app.route("/children/<int:child_id>/delete", methods=["POST"])
@verified_login_required
def delete_child(child_id):
    child = db.session.get(Child, child_id)
    if not child or child.parent_id != current_user().id:
        return "Access denied", 403
    child.active = False
    db.session.commit()
    flash(f"{child.name} was removed from your active children.")
    return redirect(request.referrer or url_for("profile"))


@app.route("/children/<int:child_id>/lifts")
@verified_login_required
def child_lifts(child_id):
    user = current_user()
    child = db.session.get(Child, child_id)
    if not child or child.parent_id != user.id or not child.active:
        return "Child not found or access denied", 404
    bookings = (ShareBooking.query.filter_by(child_id=child.id, rider_id=user.id, status="confirmed")
                .join(Availability, ShareBooking.driver_availability_id == Availability.id)
                .filter(Availability.start_time >= datetime.now() - timedelta(days=1))
                .order_by(Availability.start_time).all())
    return render_template("child_lifts.html", user=user, child=child, bookings=bookings)


@app.route("/dashboard")
@verified_login_required
def dashboard():
    user = current_user()
    monday = week_monday()
    week_journeys = calendar_journeys(user, monday, confirmed_only=True)
    upcoming = (ShareBooking.query.filter_by(rider_id=user.id, status="confirmed")
                .join(Availability, ShareBooking.driver_availability_id == Availability.id)
                .filter(Availability.start_time >= datetime.now())
                .order_by(Availability.start_time).limit(6).all())
    upcoming_offered = (Availability.query.filter(Availability.user_id == user.id,
                                                  Availability.start_time >= datetime.now())
                        .order_by(Availability.start_time).limit(6).all())
    return render_template("dashboard.html", user=user, pools=user.pools,
                           children=[c for c in user.children if c.active],
                           week_start=monday, week_journeys=week_journeys, upcoming=upcoming,
                           upcoming_offered=upcoming_offered,
                           remaining_seats=remaining_seats)


@app.route("/lifts")
@verified_login_required
def lifts_view():
    user = current_user()
    mine = request.args.get("mine") == "1"
    week = week_monday(request.args.get("week"))
    start_dt = datetime.combine(week, datetime.min.time())
    end_dt = start_dt + timedelta(days=7)
    pool_ids = [p.id for p in user.pools]

    if mine:
        query = (Availability.query
                 .filter(Availability.pool_id.in_(pool_ids),
                         Availability.user_id == user.id,
                         Availability.start_time >= start_dt,
                         Availability.start_time < end_dt)
                 if pool_ids else Availability.query.filter(Availability.id == -1))
        availabilities = query.order_by(Availability.start_time, Availability.id).all()
    else:
        all_rows = (Availability.query
                    .filter(Availability.pool_id.in_(pool_ids),
                            Availability.user_id != user.id,
                            Availability.start_time >= start_dt,
                            Availability.start_time < end_dt)
                    .order_by(Availability.start_time, Availability.id).all()
                    if pool_ids else [])
        # Show both sections: confirmed cars with space first, then all
        # Available-to-Drive backup offers. Keeping both sets separate makes
        # the confirmed result obvious without hiding the useful fallback runs.
        availabilities = [r for r in all_rows
                          if (r.driver_mode == "confirmed" and remaining_seats(r) > 0)
                          or (r.driver_intent or r.driver_mode) == "randomized"]
        availabilities.sort(key=lambda a: (a.start_time, a.pool_id, a.id))

    time_slots = weekly_time_slots(availabilities)
    prev_week = week - timedelta(days=7)
    next_week = week + timedelta(days=7)
    return render_template("lifts.html", user=user, availabilities=availabilities, mine=mine,
                           children=[c for c in user.children if c.active],
                           remaining_seats=remaining_seats, journey_passengers=journey_passengers,
                           matching_driver_options=matching_driver_options,
                           week_start=week, prev_week=prev_week, next_week=next_week,
                           time_slots=time_slots,
                           confirmed_availabilities=[a for a in availabilities if a.driver_mode == "confirmed"],
                           backup_availabilities=[a for a in availabilities if a.driver_mode != "confirmed"])


@app.route("/calendar")
@verified_login_required
def calendar_view():
    user = current_user()
    monday = week_monday(request.args.get("week"))
    journeys = calendar_journeys(user, monday, confirmed_only=True)
    start_dt = datetime.combine(monday, datetime.min.time())
    end_dt = start_dt + timedelta(days=7)
    booked = (ShareBooking.query.filter_by(rider_id=user.id, status="confirmed")
              .join(Availability, ShareBooking.driver_availability_id == Availability.id)
              .filter(Availability.start_time >= start_dt, Availability.start_time < end_dt)
              .order_by(Availability.start_time).all())
    prev_week = monday - timedelta(days=7)
    next_week = monday + timedelta(days=7)
    view = request.args.get("view", "grid")
    if view not in {"grid", "list"}:
        view = "grid"
    response = render_template("calendar.html", user=user, week_start=monday, journeys=journeys, booked=booked,
                               prev_week=prev_week, next_week=next_week, view=view,
                               pools=user.pools, remaining_seats=remaining_seats,
                               journey_passengers=journey_passengers)
    from flask import make_response
    response = make_response(response)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/calendar/add", methods=["POST"])
@verified_login_required
def add_calendar_batch():
    user = current_user()
    pool_id = int(request.form["pool_id"])
    pool = db.session.get(Pool, pool_id)
    if not pool or not user_in_pool(user, pool):
        return "Access denied", 403

    week = week_monday(request.form.get("week_start"))
    selected_days = request.form.getlist("days")
    start_time = request.form["start_time"]
    try:
        parsed_time = parse_start_time(start_time)
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("calendar_view", week=week.isoformat()))
    seats = int(request.form["seats_available"])
    driver_mode = request.form.get("driver_mode", "confirmed")
    if driver_mode not in {"confirmed", "randomized"}:
        driver_mode = "confirmed"
    driver = selected_driver_for(pool, user, request.form.get("driver_id"))
    if not driver:
        flash("Please choose a valid driver for this Pool.")
        return redirect(url_for("calendar_view", week=week.isoformat()))
    child_assignment = request.form.get("child_assignment", "now")
    if child_assignment not in {"now", "later"}:
        child_assignment = "now"
    if driver.id == user.id:
        if user.driver_only_availability:
            child_assignment = "later"
        user.child_assignment_preference = child_assignment
    child_ids_for_driver = request.form.getlist("child_ids") if driver.id == user.id else []
    # Calendar defaults to all active children for the logged-in parent unless
    # they explicitly choose Just the driver. This keeps the simple default
    # while allowing each lift/time slot to have a different child selection.
    if driver.id == user.id and child_assignment == "now" and not child_ids_for_driver:
        child_ids_for_driver = [str(c.id) for c in driver.children if c.active]
    if driver.id == user.id and child_assignment == "now" and not child_ids_for_driver:
        flash("Please choose at least one child, or choose 'None needed' under the Family tab and update your profile.")
        return redirect(url_for("calendar_view", week=week.isoformat(), **{"_anchor": "add-availability"}))
    added = 0

    for day_index in selected_days:
        day = week + timedelta(days=int(day_index))
        start = datetime.combine(day, parsed_time)
        end = finish_30_minutes(start)
        if seats < 1:
            continue
        duplicate = Availability.query.filter_by(pool_id=pool.id, user_id=driver.id,
                                                   start_time=start, end_time=end).first()
        if not duplicate:
            availability = Availability(pool_id=pool.id, user_id=driver.id,
                                        start_time=start, end_time=end,
                                        seats_available=seats, is_driver=True, driver_mode=driver_mode,
                                        driver_intent=driver_mode,
                                        pickup_location=request.form.get("pickup_location", "").strip() or None)
            db.session.add(availability)
            db.session.flush()
            if driver_mode == "confirmed":
                # Own children are selected by default, but the parent can explicitly choose
                # “None needed” when they do not want any of their children on this run.
                set_driver_children_for_slot(availability, driver, child_ids_for_driver if child_assignment == "now" else [])
            else:
                set_driver_children_for_slot(availability, driver, child_ids_for_driver if child_assignment == "now" else [])
                create_randomized_spots(pool, availability, driver)
            rebalance_slot(pool.id, start, end)
            added += 1

    db.session.commit()
    flash(f"Added {added} lift day(s).")
    return redirect(url_for("calendar_view", week=week.isoformat()))


@app.route("/calendar/copy-next-week", methods=["POST"])
@verified_login_required
def copy_next_week():
    user = current_user()
    source_week = week_monday(request.form.get("week_start"))
    source = [a for a in calendar_data(user, source_week) if a.user_id == user.id]
    added = 0
    for a in source:
        start = a.start_time + timedelta(days=7)
        end = a.end_time + timedelta(days=7)
        duplicate = Availability.query.filter_by(pool_id=a.pool_id, user_id=user.id,
                                                   start_time=start, end_time=end).first()
        if not duplicate:
            copied_intent = a.driver_intent or a.driver_mode
            copied = Availability(pool_id=a.pool_id, user_id=user.id,
                                  start_time=start, end_time=end,
                                  seats_available=a.seats_available, is_driver=a.is_driver,
                                  # A copied Available-to-Drive slot is a fresh backup
                                  # offer and must be re-entered into fair rotation for the new week.
                                  driver_mode="confirmed" if copied_intent == "confirmed" else "randomized",
                                  driver_intent=copied_intent,
                                  pickup_location=a.pickup_location)
            db.session.add(copied)
            db.session.flush()
            if copied.driver_mode == "confirmed":
                set_driver_children_for_slot(copied, user, [c.id for c in user.children if c.active])
            else:
                create_randomized_spots(db.session.get(Pool, copied.pool_id), copied, user)
            rebalance_slot(copied.pool_id, start, end)
            added += 1
    db.session.commit()
    target = source_week + timedelta(days=7)
    flash(f"Copied {added} availability slot(s) to next week.")
    return redirect(url_for("calendar_view", week=target.isoformat()))


@app.route("/availability/<int:availability_id>/delete", methods=["POST"])
@verified_login_required
def delete_availability(availability_id):
    user = current_user()
    a = db.session.get(Availability, availability_id)
    if not a or a.user_id != user.id:
        return "Access denied", 403

    pool = a.pool
    old_start, old_end = a.start_time, a.end_time
    was_confirmed = a.driver_mode == "confirmed"
    bookings = ShareBooking.query.filter_by(driver_availability_id=a.id, status="confirmed").all()
    replacement = None

    if was_confirmed:
        # First look for another already-confirmed car with enough vacant
        # places. If there isn't one, promote a backup specifically for this
        # exact slot before removing the old driver.
        alternatives = [r for r in _slot_rows(pool.id, old_start, old_end)
                        if r.id != a.id and r.driver_mode == "confirmed"
                        and remaining_seats(r) >= len([b for b in bookings if b.rider_id != r.user_id])]
        if alternatives:
            alternatives.sort(key=lambda r: (confirmed_drive_count(r.user, r.pool_id), r.id))
            replacement = alternatives[0]
        else:
            replacement = _promote_backup_for_slot(pool.id, old_start, old_end)

    if replacement:
        old_driver_name = a.user.name
        # Move every booked child/passenger from the deleted driver to the
        # replacement car before deleting the old availability. This keeps the
        # whole exact time slot together.
        for booking in bookings:
            if booking.rider and booking.rider_id != replacement.user_id:
                send_lift_reassignment_notification(
                    booking.rider, old_driver_name, replacement.user.name,
                    replacement.start_time, replacement.end_time,
                    replacement.pickup_location
                )
            booking.driver_availability_id = replacement.id
            booking.pool_id = replacement.pool_id

        db.session.delete(a)
        db.session.flush()
        replacement.driver_mode = "confirmed"
        _ensure_confirmed_driver_children(replacement)
        # Rebuild the exact slot while explicitly protecting the promoted/
        # replacement driver from being rotated away on this deletion.
        rebalance_slot(pool.id, old_start, old_end)
        replacement = db.session.get(Availability, replacement.id)
        if replacement and replacement.driver_mode != "confirmed":
            replacement.driver_mode = "confirmed"
            _ensure_confirmed_driver_children(replacement)
            match_randomized_spots(pool, replacement)
        db.session.commit()
        flash(f"Lift deleted. {replacement.user.name} is now confirmed and all booked passengers were moved to the new driver.")
        return redirect(request.referrer or url_for("calendar_view"))

    # No replacement was available. Remove the old bookings and notify the
    # affected parents, then refresh the slot so any visible backup can become
    # the new confirmed driver if one is available.
    for booking in bookings:
        if booking.rider:
            send_lift_deleted_notification(booking.rider, a.user.name, old_start, old_end, a.pickup_location)
        db.session.delete(booking)
    db.session.delete(a)
    db.session.flush()
    rebalance_slot(pool.id, old_start, old_end)
    db.session.commit()
    if bookings:
        flash("Lift deleted. Affected parents were notified because no replacement confirmed driver was available.")
    else:
        flash("Availability removed and the backup options were refreshed.")
    return redirect(request.referrer or url_for("calendar_view"))


@app.route("/pools/create", methods=["POST"])
@verified_login_required
def create_pool():
    user = current_user()
    name = request.form["name"].strip()
    pool = Pool(name=name, invite_code=secrets.token_urlsafe(12), created_by_id=user.id)
    pool.members.append(user)
    db.session.add(pool)
    db.session.commit()
    return redirect(url_for("pool_detail", pool_id=pool.id))


@app.route("/pools/<int:pool_id>/delegated-availability", methods=["POST"])
@verified_login_required
def update_delegated_availability(pool_id):
    user = current_user()
    pool = db.session.get(Pool, pool_id)
    if not pool or pool.created_by_id != user.id:
        return "Access denied", 403
    pool.allow_delegated_availability = request.form.get("allow_delegated_availability") == "1"
    db.session.commit()
    flash("Group availability permissions updated.")
    return redirect(url_for("pool_detail", pool_id=pool.id))


@app.route("/pools/join-code", methods=["POST"])
@verified_login_required
def join_pool_by_code():
    code = request.form.get("invite_code", "").strip()
    if not code:
        flash("Please enter a Pool code.")
        return redirect(url_for("dashboard"))
    pool = Pool.query.filter_by(invite_code=code).first()
    if not pool:
        flash("We couldn't find that group code. Please check it and try again.")
        return redirect(url_for("dashboard"))
    user = current_user()
    if not user_in_pool(user, pool):
        pool.members.append(user)
        db.session.commit()
        flash(f"You joined {pool.name}.")
    else:
        flash(f"You're already in {pool.name}.")
    return redirect(url_for("pool_detail", pool_id=pool.id))


@app.route("/pools/join/<invite_code>", methods=["GET", "POST"])
@verified_login_required
def join_pool(invite_code):
    user = current_user()
    pool = Pool.query.filter_by(invite_code=invite_code).first_or_404()
    if user_in_pool(user, pool):
        return redirect(url_for("pool_detail", pool_id=pool.id))
    if request.method == "POST":
        pool.members.append(user)
        db.session.commit()
        return redirect(url_for("pool_detail", pool_id=pool.id))
    return render_template("join_pool.html", pool=pool)


@app.route("/pools/<int:pool_id>")
@verified_login_required
def pool_detail(pool_id):
    user = current_user()
    pool = db.session.get(Pool, pool_id)
    if not pool or not user_in_pool(user, pool):
        return "Pool not found or access denied", 403
    mine = request.args.get("mine") == "1"
    if mine:
        availabilities = (Availability.query.filter_by(pool_id=pool.id, user_id=user.id)
                          .order_by(Availability.start_time).all())
    else:
        availabilities = visible_lifts_for_pool(user, pool)
    bookings = ShareBooking.query.filter_by(pool_id=pool.id, rider_id=user.id, status="confirmed").order_by(ShareBooking.requested_at.desc()).all()
    randomized_spots = (RandomizedSpot.query.filter_by(pool_id=pool.id).order_by(RandomizedSpot.created_at).all())
    return render_template("pool.html", pool=pool, user=user, availabilities=availabilities, mine=mine,
                           bookings=bookings, children=[c for c in user.children if c.active],
                           pool_members=pool.members, remaining_seats=remaining_seats,
                           matching_driver_options=matching_driver_options, randomized_spots=randomized_spots)


@app.route("/pools/<int:pool_id>/availability", methods=["POST"])
@verified_login_required
def add_availability(pool_id):
    user = current_user(); pool = db.session.get(Pool, pool_id)
    if not pool or not user_in_pool(user, pool): return "Access denied", 403
    lift_date = request.form["lift_date"]
    start_time = request.form["start_time"]
    try:
        parsed_time = parse_start_time(start_time)
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("pool_detail", pool_id=pool.id))
    start = datetime.combine(datetime.fromisoformat(lift_date).date(), parsed_time)
    end = finish_30_minutes(start)
    seats = int(request.form["seats_available"])
    driver_mode = request.form.get("driver_mode", "confirmed")
    if driver_mode not in {"confirmed", "randomized"}:
        driver_mode = "confirmed"
    driver = selected_driver_for(pool, user, request.form.get("driver_id"))
    if not driver or seats < 1:
        flash("Please choose a valid driver and at least one passenger seat.")
        return redirect(url_for("pool_detail", pool_id=pool.id))
    child_assignment = request.form.get("child_assignment", "now")
    if child_assignment not in {"now", "later"}:
        child_assignment = "now"
    if driver.id == user.id:
        if user.driver_only_availability:
            child_assignment = "later"
        user.child_assignment_preference = child_assignment
    child_ids_for_driver = request.form.getlist("child_ids") if driver.id == user.id else []
    if driver.id == user.id and child_assignment == "now" and not child_ids_for_driver:
        child_ids_for_driver = [str(c.id) for c in driver.children if c.active]
    if driver.id == user.id and child_assignment == "now" and not child_ids_for_driver:
        flash("Please choose at least one child, or choose 'None needed' under the Family tab and update your profile.")
        return redirect(url_for("pool_detail", pool_id=pool.id))
    availability = Availability(pool_id=pool.id, user_id=driver.id, start_time=start,
                                end_time=end, seats_available=seats, is_driver=True, driver_mode=driver_mode,
                                driver_intent=driver_mode,
                                pickup_location=request.form.get("pickup_location", "").strip() or None)
    db.session.add(availability)
    db.session.flush()
    if driver_mode == "confirmed":
        # Own children are selected by default, but the parent can explicitly choose
        # “None needed” when they do not want any of their children on this run.
        set_driver_children_for_slot(availability, driver, child_ids_for_driver if child_assignment == "now" else [])
    else:
        set_driver_children_for_slot(availability, driver, child_ids_for_driver if child_assignment == "now" else [])
        create_randomized_spots(pool, availability, driver)
    rebalance_slot(pool.id, start, end)
    db.session.commit()
    if driver_mode == "randomized" and not child_ids_for_driver and driver.id == user.id:
        flash("Availability added. You can add your named children later from My offered lifts.")
    elif availability.driver_mode == "randomized":
        flash("Availability added as an offered backup run. The confirmed driver remains on the calendar.")
    else:
        flash("Availability added as the confirmed run.")
    return redirect(url_for("pool_detail", pool_id=pool.id))


@app.route("/pools/<int:pool_id>/availability/<int:availability_id>/children", methods=["POST"])
@verified_login_required
def update_availability_children(pool_id, availability_id):
    user = current_user()
    pool = db.session.get(Pool, pool_id)
    availability = db.session.get(Availability, availability_id)
    if not pool or not availability or availability.pool_id != pool.id or not user_in_pool(user, pool):
        return "Access denied", 403
    if availability.user_id != user.id:
        return "Only the driver can manage their named passengers", 403
    child_ids = request.form.getlist("child_ids")
    if availability.driver_mode == "confirmed" or (availability.driver_intent or availability.driver_mode) == "confirmed":
        child_ids = [str(c.id) for c in user.children if c.active]
    set_driver_children_for_slot(availability, user, child_ids)
    rebalance_slot(pool.id, availability.start_time, availability.end_time)
    db.session.commit()
    flash("Named passengers updated.")
    return redirect(request.referrer or url_for("lifts_view", mine=1))


@app.route("/pools/<int:pool_id>/availability/<int:availability_id>/edit", methods=["POST"])
@verified_login_required
def edit_availability(pool_id, availability_id):
    user = current_user()
    pool = db.session.get(Pool, pool_id)
    availability = db.session.get(Availability, availability_id)
    if not pool or not availability or availability.pool_id != pool.id or availability.user_id != user.id or not user_in_pool(user, pool):
        return "Access denied", 403

    old_start, old_end = availability.start_time, availability.end_time
    old_pickup = availability.pickup_location
    booked = (ShareBooking.query.filter_by(driver_availability_id=availability.id, status="confirmed")
              .filter(ShareBooking.rider_id != user.id).all())
    passenger_parents = {}
    for booking in booked:
        passenger_parents[booking.rider_id] = booking.rider

    lift_date = request.form.get("lift_date", "")
    start_value = request.form.get("start_time", "")
    try:
        start = datetime.combine(datetime.fromisoformat(lift_date).date(), parse_start_time(start_value))
    except (ValueError, TypeError):
        flash("Please choose a valid date and start time.")
        return redirect(url_for("pool_detail", pool_id=pool.id, mine=1))
    end = finish_30_minutes(start)
    try:
        seats = int(request.form.get("seats_available", "0"))
    except ValueError:
        seats = 0
    booked_count = already_booked_seats(availability)
    if seats < max(1, booked_count):
        flash(f"You cannot reduce the vacant spots below the {booked_count} place(s) already booked.")
        return redirect(url_for("pool_detail", pool_id=pool.id, mine=1))

    availability.start_time = start
    availability.end_time = end
    availability.seats_available = seats
    availability.pickup_location = request.form.get("pickup_location", "").strip() or None
    child_ids = request.form.getlist("child_ids")
    if (availability.driver_intent or availability.driver_mode) == "confirmed":
        child_ids = [str(c.id) for c in user.children if c.active]
    set_driver_children_for_slot(availability, user, child_ids)
    rebalance_slot(pool.id, start, end)
    db.session.commit()

    new_pickup = request.form.get("pickup_location", "").strip() or None
    changed = (old_start != start or old_end != end or old_pickup != new_pickup)
    alternative_driver = Availability.query.filter(
        Availability.pool_id == pool.id,
        Availability.driver_mode == "confirmed",
        Availability.is_driver == True,
        Availability.id != availability.id,
        Availability.start_time == old_start,
        Availability.end_time == old_end,
    ).first()
    notified = 0
    if booked and changed and not alternative_driver:
        for recipient in passenger_parents.values():
            if send_lift_change_notification(recipient, user.name, old_start, old_end, start, end, availability.pickup_location):
                notified += 1

    flash("Lift updated.")
    if notified:
        flash(f"We emailed {notified} booked parent(s) because no other confirmed driver was assigned to the original journey.")
    return redirect(url_for("pool_detail", pool_id=pool.id, mine=1))


@app.route("/pools/<int:pool_id>/availability/<int:availability_id>/change-driver", methods=["POST"])
@verified_login_required
def change_driver(pool_id, availability_id):
    user = current_user(); pool = db.session.get(Pool, pool_id); availability = db.session.get(Availability, availability_id)
    if not pool or not availability or availability.pool_id != pool.id or not user_in_pool(user, pool):
        return "Access denied", 403
    if availability.driver_mode != "confirmed":
        flash("A randomised slot is waiting to be matched rather than having a fixed driver.")
        return redirect(request.referrer or url_for("pool_detail", pool_id=pool.id))
    target_id = request.form.get("driver_id")
    target = db.session.get(User, int(target_id)) if target_id and target_id.isdigit() else None
    if not target or not user_in_pool(target, pool) or target.id == availability.user_id:
        flash("Please choose another parent in this Pool.")
        return redirect(request.referrer or url_for("pool_detail", pool_id=pool.id))
    target_slot = Availability.query.filter_by(pool_id=pool.id, user_id=target.id,
                                               start_time=availability.start_time, end_time=availability.end_time,
                                               driver_mode="confirmed").first()
    if not target_slot:
        flash("That parent has not marked themselves as available for this exact time slot.")
        return redirect(request.referrer or url_for("pool_detail", pool_id=pool.id))
    availability.user_id, target_slot.user_id = target_slot.user_id, availability.user_id
    db.session.commit()
    flash(f"The driver for this time slot has been swapped with {target.name}.")
    return redirect(request.referrer or url_for("pool_detail", pool_id=pool.id))


@app.route("/pools/<int:pool_id>/join/<int:availability_id>", methods=["POST"])
@verified_login_required
def join_available_lift(pool_id, availability_id):
    user = current_user(); pool = db.session.get(Pool, pool_id); availability = db.session.get(Availability, availability_id)
    if not pool or not availability or availability.pool_id != pool.id or not user_in_pool(user, pool): return "Access denied", 403
    if availability.user_id == user.id:
        flash("You cannot join your own car.")
        return redirect(url_for("pool_detail", pool_id=pool.id))
    child_ids = request.form.getlist("child_ids")
    single_child_id = request.form.get("child_id")
    if single_child_id:
        child_ids = [single_child_id]
    try:
        child_ids = [int(x) for x in child_ids]
    except (TypeError, ValueError):
        child_ids = []
    children = [db.session.get(Child, cid) for cid in child_ids]
    children = [c for c in children if c and c.parent_id == user.id and c.active]
    if not children:
        flash("Please add a child to your family profile, then choose who is travelling.")
        return redirect(url_for("profile"))
    if len(children) > remaining_seats(availability):
        # If another confirmed car at the same exact time has capacity, use it
        # before creating another confirmed driver. Only promote a backup when
        # every confirmed car is full.
        alternatives = [r for r in _slot_rows(pool.id, availability.start_time, availability.end_time)
                        if r.driver_mode == "confirmed" and r.id != availability.id
                        and r.user_id != user.id and remaining_seats(r) >= len(children)]
        if alternatives:
            alternatives.sort(key=lambda r: (_driver_distance_to_user(r.user, user),
                                             confirmed_drive_count(r.user, r.pool_id), r.id))
            availability = alternatives[0]
        else:
            promoted = _promote_backup_for_slot(pool.id, availability.start_time, availability.end_time)
            if promoted:
                reconcile_slot_drivers(pool.id, availability.start_time, availability.end_time)
                db.session.flush()
                availability = promoted
        if len(children) > remaining_seats(availability):
            flash(f"There are only {remaining_seats(availability)} seat(s) left in this car.")
            return redirect(url_for("pool_detail", pool_id=pool.id))

    added_children = []
    for child in children:
        # A child may have a lift in several different time slots.
        # Only treat it as a duplicate when the child is already booked for THIS exact time slot.
        existing = (ShareBooking.query
                    .join(Availability, ShareBooking.driver_availability_id == Availability.id)
                    .filter(ShareBooking.pool_id == pool.id,
                            ShareBooking.child_id == child.id,
                            ShareBooking.status == "confirmed",
                            Availability.start_time == availability.start_time,
                            Availability.end_time == availability.end_time)
                    .first())
        if existing:
            continue
        db.session.add(ShareBooking(pool_id=pool.id, driver_availability_id=availability.id,
                                    rider_id=user.id, child_id=child.id))
        added_children.append(child)

    if added_children:
        # Count a shared journey once, rather than once per passenger child.
        availability.user.completed_shares += 1
        reconcile_slot_drivers(pool.id, availability.start_time, availability.end_time)
        for confirmed_row in _slot_rows(pool.id, availability.start_time, availability.end_time):
            if confirmed_row.driver_mode == "confirmed":
                match_randomized_spots(pool, confirmed_row)
        db.session.commit()
        left = remaining_seats(availability)
        names = ", ".join(c.name for c in added_children)
        flash(f"{names} joined {availability.user.name}'s lift. {left} seat(s) remain.")
    else:
        flash("Those children already have confirmed places in this Pool.")
    return redirect(url_for("pool_detail", pool_id=pool.id))


# Legacy/manual matching routes remain available for experimentation.
@app.route("/pools/<int:pool_id>/find", methods=["POST"])
@verified_login_required
def find_ride(pool_id):
    user = current_user(); pool = db.session.get(Pool, pool_id)
    if not pool or not user_in_pool(user, pool): return "Access denied", 403
    start = datetime.fromisoformat(request.form["start_time"]); end = datetime.fromisoformat(request.form["end_time"])
    matches = find_matches(pool, user, start, end)
    return render_template("matches.html", pool=pool, matches=matches, start=start, end=end, math=math)


@app.route("/api/pools/<int:pool_id>/matches")
@verified_login_required
def api_matches(pool_id):
    user = current_user(); pool = db.session.get(Pool, pool_id)
    if not pool or not user_in_pool(user, pool): return jsonify({"error": "Access denied"}), 403
    start = datetime.fromisoformat(request.args["start"]); end = datetime.fromisoformat(request.args["end"])
    matches = find_matches(pool, user, start, end)
    return jsonify([{
        "availability_id": item["availability"].id,
        "driver": item["availability"].user.name,
        "driver_completed_shares": item["completed_shares"],
        "remaining_seats": item["remaining_seats"],
        "distance_km": None if not math.isfinite(item["distance_km"]) else round(item["distance_km"], 2),
        "start": item["availability"].start_time.isoformat(),
        "end": item["availability"].end_time.isoformat(),
    } for item in matches])


@app.route("/admin/demo-seed")
def demo_seed():
    """Demo helper for local development only. Remove/protect before production."""
    if User.query.count() > 0:
        return "Demo data already exists."
    alice = User(name="Alice (Parent)", email="alice@example.com", email_verified_at=datetime.utcnow(), location_name="School area", latitude=53.4084, longitude=-2.9916)
    bob = User(name="Bob (Parent)", email="bob@example.com", email_verified_at=datetime.utcnow(), location_name="Nearby", latitude=53.4100, longitude=-2.9900)
    cara = User(name="Cara (Parent)", email="cara@example.com", email_verified_at=datetime.utcnow(), latitude=53.4150, longitude=-2.9850)
    for u in (alice, bob, cara): u.set_password("password123")
    db.session.add_all([alice, bob, cara]); db.session.flush()
    db.session.add_all([Child(parent_id=alice.id, name="Ella", year_group="Year 7"), Child(parent_id=alice.id, name="Noah", year_group="Year 5"), Child(parent_id=cara.id, name="Sophie", year_group="Year 7")])
    pool = Pool(name="School Run", invite_code=secrets.token_urlsafe(8), created_by_id=alice.id)
    pool.members.extend([alice, bob, cara]); db.session.add(pool); db.session.flush()
    monday = week_monday()
    for i in range(5):
        day = monday + timedelta(days=i)
        db.session.add(Availability(pool_id=pool.id, user_id=bob.id,
                                    start_time=datetime.combine(day, datetime.min.time()).replace(hour=7, minute=30),
                                    end_time=datetime.combine(day, datetime.min.time()).replace(hour=8, minute=30),
                                    seats_available=3, is_driver=True))
    db.session.commit()
    return redirect(url_for("login"))


with app.app_context():
    db.create_all()
    # Lightweight prototype migration for existing local SQLite databases.
    from sqlalchemy import inspect
    columns = {c["name"] for c in inspect(db.engine).get_columns("pool")}
    if "allow_delegated_availability" not in columns:
        db.session.execute(db.text("ALTER TABLE pool ADD COLUMN allow_delegated_availability BOOLEAN NOT NULL DEFAULT TRUE"))
        db.session.commit()
    user_columns = {c["name"] for c in inspect(db.engine).get_columns("user")}
    if "terms_accepted_at" not in user_columns:
        db.session.execute(db.text('ALTER TABLE "user" ADD COLUMN terms_accepted_at TIMESTAMP'))
    if "share_passenger_preference" not in user_columns:
        db.session.execute(db.text("ALTER TABLE \"user\" ADD COLUMN share_passenger_preference VARCHAR(20) NOT NULL DEFAULT 'children'"))
    if "child_assignment_preference" not in user_columns:
        db.session.execute(db.text("ALTER TABLE \"user\" ADD COLUMN child_assignment_preference VARCHAR(20) NOT NULL DEFAULT 'later'"))
    if "named_share_space" not in user_columns:
        db.session.execute(db.text('ALTER TABLE "user" ADD COLUMN named_share_space BOOLEAN NOT NULL DEFAULT FALSE'))
    if "driver_only_availability" not in user_columns:
        db.session.execute(db.text('ALTER TABLE "user" ADD COLUMN driver_only_availability BOOLEAN NOT NULL DEFAULT FALSE'))
    if "named_passenger_child_ids" not in user_columns:
        db.session.execute(db.text("ALTER TABLE \"user\" ADD COLUMN named_passenger_child_ids VARCHAR(1000) NOT NULL DEFAULT ''"))
    if "email_verified_at" not in user_columns:
        db.session.execute(db.text('ALTER TABLE "user" ADD COLUMN email_verified_at TIMESTAMP'))
    availability_columns = {c["name"] for c in inspect(db.engine).get_columns("availability")}
    if "driver_mode" not in availability_columns:
        db.session.execute(db.text("ALTER TABLE availability ADD COLUMN driver_mode VARCHAR(20) NOT NULL DEFAULT 'confirmed'"))
    if "driver_intent" not in availability_columns:
        db.session.execute(db.text("ALTER TABLE availability ADD COLUMN driver_intent VARCHAR(20) NOT NULL DEFAULT 'confirmed'"))
    # Existing flexible offers pre-date driver_intent. Preserve their original
    # flexible status so the new confirmed-driver selection logic can evaluate
    # them correctly.
    db.session.execute(db.text("UPDATE availability SET driver_intent = 'randomized' WHERE driver_mode = 'randomized'"))
    if "pickup_location" not in availability_columns:
        db.session.execute(db.text("ALTER TABLE availability ADD COLUMN pickup_location VARCHAR(255)"))
    db.session.commit()
    # Migrate any older database that may have multiple confirmed offers for
    # the exact same Pool/date/time into one confirmed driver plus offered backups.
    existing_slots = (db.session.query(Availability.pool_id, Availability.start_time, Availability.end_time)
                      .group_by(Availability.pool_id, Availability.start_time, Availability.end_time).all())
    for pool_id, start_time, end_time in existing_slots:
        normalize_slot_drivers(pool_id, start_time, end_time)
    db.session.commit()


if __name__ == "__main__":
    app.run(debug=True)
