from sqlalchemy.orm import Session as DbSession

from app.models.api_key import ApiKey
from app.models.base import utcnow
from app.utils.security import generate_api_key, sha256_hex


def list_api_keys(db: DbSession) -> list[ApiKey]:
    return db.query(ApiKey).order_by(ApiKey.created_at.desc()).all()


def get_api_key(db: DbSession, api_key_id: int) -> ApiKey | None:
    return db.get(ApiKey, api_key_id)


def create_api_key(db: DbSession, name: str, user_id: int) -> tuple[ApiKey, str]:
    token, prefix, key_hash = generate_api_key()
    api_key = ApiKey(name=name, key_prefix=prefix, key_hash=key_hash, created_by_user_id=user_id)
    db.add(api_key)
    db.commit()
    db.refresh(api_key)
    return api_key, token


def revoke(db: DbSession, api_key: ApiKey) -> None:
    api_key.revoked_at = utcnow()
    db.commit()


def verify_token(db: DbSession, token: str) -> ApiKey | None:
    key_hash = sha256_hex(token)
    api_key = db.query(ApiKey).filter(ApiKey.key_hash == key_hash).first()
    if not api_key or api_key.revoked_at is not None:
        return None
    api_key.last_used_at = utcnow()
    db.commit()
    return api_key
