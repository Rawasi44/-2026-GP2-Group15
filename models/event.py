from extensions import db


class Event(db.Model):
    __tablename__ = "events"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    city = db.Column(db.String(100), default="Riyadh")
    start_date = db.Column(db.Date, nullable=False)
    time = db.Column(db.String(50), nullable=True)
    description = db.Column(db.Text, nullable=False)
    image = db.Column(db.String(300), nullable=True)
    official_link = db.Column(db.String(300), nullable=True)
    source = db.Column(db.String(100), default="GoSaudis")
    price = db.Column(db.String(100), nullable=True)
    location = db.Column(db.String(200), nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
