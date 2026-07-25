from app.config import settings
from app.database import SessionLocal
from app.models.user import User
from app.services import account_service, auth_service


def seed_admin_user() -> None:
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            auth_service.create_user(db, settings.admin_email, settings.admin_password)
            print(f"Seeded bootstrap admin user: {settings.admin_email}")
    finally:
        db.close()


def seed_chart_of_accounts() -> None:
    db = SessionLocal()
    try:
        account_service.seed_default_chart_of_accounts(db)
    finally:
        db.close()


def main() -> None:
    seed_admin_user()
    seed_chart_of_accounts()


if __name__ == "__main__":
    main()
