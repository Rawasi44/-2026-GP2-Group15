from app import create_app
from extensions import db
from models.user import User

app = create_app()

admins = [
    {
        "fullname": "Admin 1",
        "email": "admin1@afaq.com",
        "password": "Admin1234",
        "role": "admin"
    },
    {
        "fullname": "Admin 2",
        "email": "admin2@afaq.com",
        "password": "Admin1234",
        "role": "admin"
    },
    {
        "fullname": "Admin 3",
        "email": "admin3@afaq.com",
        "password": "Admin1234",
        "role": "admin"
    },
    {
        "fullname": "Admin 4",
        "email": "admin4@afaq.com",
        "password": "Admin1234",
        "role": "admin"
    }
]

with app.app_context():
    for admin_data in admins:
        existing_user = User.query.filter_by(email=admin_data["email"]).first()

        if existing_user:
            print(f"Skipped: {admin_data['email']} already exists")
            continue

        admin = User(
            fullname=admin_data["fullname"],
            email=admin_data["email"],
            role=admin_data["role"],
            is_verified=True  # Admins are provisioned directly, not through public signup.
        )

        admin.set_password(admin_data["password"])  # hashing happens here

        db.session.add(admin)
        print(f"Added: {admin_data['email']}")

    db.session.commit()
    print("Done creating admin accounts.")