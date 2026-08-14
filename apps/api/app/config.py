from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "JobAgent"
    app_env: str = "development"
    debug: bool = True

    database_url: str = "postgresql+psycopg://jobagent:jobagent@localhost:5432/jobagent"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    jwt_secret_key: str = "change-me-in-production-jobagent-dev-secret"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24

    upload_dir: str = "uploads"
    max_upload_bytes: int = 5 * 1024 * 1024  # 5 MB

    google_api_key: str | None = None
    gemini_model: str = "gemini-flash-latest"
    embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 768

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Job scanner (Celery beat every hour)
    scanner_enabled: bool = True
    scanner_keywords: str = "python developer"
    scanner_locations: str = "bangalore"
    scanner_portals: str = "naukri,linkedin,internshala,foundit,unstop"
    scanner_max_pages: int = 1
    scanner_headless: bool = True
    # Empty = bundled Chromium; set to "chrome" on a desktop host with Google Chrome
    scanner_browser_channel: str = ""

    # Phase 4 Stage 1 — vector matching
    match_vector_limit: int = 50
    match_min_similarity: float = 0.35
    embed_jobs_batch_size: int = 40

    # Phase 4 Stage 2 — LLM deep-score (Gemini)
    match_deep_score_limit: int = 10

    # Notifications — email (Resend HTTPS and/or SMTP) + WhatsApp (Twilio)
    resend_api_key: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_from_name: str = "JobAgent"
    smtp_use_tls: bool = True

    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_whatsapp_from: str | None = None  # e.g. whatsapp:+14155238886

    app_public_url: str = "http://localhost:5173"

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def email_configured(self) -> bool:
        if self.resend_api_key and self.smtp_from_email:
            return True
        return bool(self.smtp_host and self.smtp_from_email)

    def whatsapp_configured(self) -> bool:
        return bool(
            self.twilio_account_sid
            and self.twilio_auth_token
            and self.twilio_whatsapp_from
        )


settings = Settings()
