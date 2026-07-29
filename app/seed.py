from app.config import settings
from app.database import SessionLocal
from app.models.user import User
from app.services import account_service, auth_service, invoice_settings_service


def seed_admin_user() -> None:
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            auth_service.create_user(db, settings.admin_email, settings.admin_password, is_admin=True)
            print(f"Seeded bootstrap admin user: {settings.admin_email}")
    finally:
        db.close()


def seed_chart_of_accounts() -> None:
    db = SessionLocal()
    try:
        account_service.seed_default_chart_of_accounts(db)
    finally:
        db.close()


def backfill_invoice_settings_mappings() -> None:
    # Runs after seed_chart_of_accounts so the pre-existing default accounts
    # (e.g. Service Revenue) exist by the time this looks them up — see
    # invoice_settings_service.backfill_default_mappings for why this can't
    # happen entirely inside a migration.
    db = SessionLocal()
    try:
        invoice_settings_service.backfill_default_mappings(db)
    finally:
        db.close()


def main() -> None:
    seed_admin_user()
    seed_chart_of_accounts()
    backfill_invoice_settings_mappings()


if __name__ == "__main__":
    main()
