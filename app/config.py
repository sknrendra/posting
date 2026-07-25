from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/posting.db"
    session_secret: str = "dev-secret-change-me"
    session_cookie_name: str = "posting_session"
    session_cookie_secure: bool = False
    session_lifetime_days: int = 14
    admin_email: str = "admin@example.com"
    admin_password: str = "changeme123"

    class Config:
        env_file = ".env"


settings = Settings()
