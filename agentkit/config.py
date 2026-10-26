"""Application configuration loaded from environment variables using Pydantic Settings."""

from collections.abc import Mapping
from functools import lru_cache
from typing import Literal
from urllib.parse import SplitResult, quote, urlsplit, urlunsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL as SQLAlchemyURL

#: Development defaults. They also fill in the components a deployment omits, so a
#: component-only configuration resolves to exactly the same development URL.
DEFAULT_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/agentkit"
DEFAULT_POSTGRES_HOST = "localhost"
DEFAULT_POSTGRES_PORT = 5432
DEFAULT_POSTGRES_DB = "agentkit"
DEFAULT_POSTGRES_USER = "postgres"
DEFAULT_READONLY_USER = "agentkit_readonly"


def apply_redis_password(url: str, password: str | None) -> str:
    """Return ``url`` with ``password`` embedded when it carries no credentials.

    The hardened compose stack requires ``REDIS_PASSWORD`` and starts Redis with
    ``--requirepass``, but keeping the secret out of ``REDIS_URL`` is the safer way to
    configure a deployment (a URL ends up in logs, process listings and crash dumps).
    An explicit credential in the URL always wins, so pointing at a passwordless Redis
    for local work still behaves exactly as before.
    """
    if not password:
        return url

    parts: SplitResult = urlsplit(url)
    if parts.scheme not in {"redis", "rediss"}:
        return url
    # An existing userinfo (``redis://user@host``) or password must never be overridden.
    if "@" in parts.netloc or parts.password:
        return url
    if not parts.netloc:
        return url

    userinfo = f":{quote(password, safe='')}"
    return urlunsplit(parts._replace(netloc=f"{userinfo}@{parts.netloc}"))


def build_postgres_url(
    *,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
    drivername: str = "postgresql+asyncpg",
) -> str:
    """Render a PostgreSQL URL with every component percent-encoded.

    A password from ``openssl rand -base64 24`` contains ``/``, ``+`` and ``=``. Interpolated
    into a URL string by hand, a ``/`` ends the authority: the remainder of the password
    becomes part of the path and the connection resolves to a database that does not exist.
    Building the URL through SQLAlchemy's URL object escapes each component, so any value a
    secret generator produces is usable as-is.

    Returns:
        A ``postgresql+asyncpg`` URL with the credentials escaped, password included.
    """
    return SQLAlchemyURL.create(
        drivername=drivername,
        username=username,
        password=password,
        host=host,
        port=port,
        database=database,
    ).render_as_string(hide_password=False)


def resolve_postgres_url(
    env: Mapping[str, str],
    *,
    url_var: str,
    user_var: str,
    password_var: str,
    default_user: str,
) -> str | None:
    """Resolve a PostgreSQL URL from an explicit URL variable or from components.

    An explicit URL is authoritative and returned verbatim, so a deployment that pins a full
    URL (managed database, secret-manager substitution) behaves exactly as before. Otherwise
    the URL is assembled from ``POSTGRES_HOST``/``POSTGRES_PORT``/``POSTGRES_DB`` plus the
    role variables, which is the only way to build it safely when the password is arbitrary.

    Args:
        env: Environment mapping holding the variables.
        url_var: Name of the variable that may hold a complete URL.
        user_var: Name of the variable holding the role name.
        password_var: Name of the variable holding the role password.
        default_user: Role name used when ``user_var`` is unset.

    Returns:
        The resolved URL, or None when neither an explicit URL nor a password is configured.

    Raises:
        ValueError: If ``POSTGRES_PORT`` is set but is not an integer.
    """
    explicit = (env.get(url_var) or "").strip()
    if explicit:
        return explicit

    password = (env.get(password_var) or "").strip()
    if not password:
        return None

    port_text = (env.get("POSTGRES_PORT") or "").strip() or str(DEFAULT_POSTGRES_PORT)
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError(f"POSTGRES_PORT must be an integer, got {port_text!r}.") from exc

    return build_postgres_url(
        host=(env.get("POSTGRES_HOST") or "").strip() or DEFAULT_POSTGRES_HOST,
        port=port,
        database=(env.get("POSTGRES_DB") or "").strip() or DEFAULT_POSTGRES_DB,
        username=(env.get(user_var) or "").strip() or default_user,
        password=password,
    )


class Settings(BaseSettings):
    """Global configuration settings for AgentKit."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database and Cache
    DATABASE_URL: str = Field(
        default=DEFAULT_DATABASE_URL,
        description=(
            "Async PostgreSQL connection URL. Optional: when it is unset, it is built from "
            "the POSTGRES_* components below and an explicit value always wins."
        ),
    )
    DATABASE_READONLY_URL: str | None = Field(
        default=None,
        description=(
            "Async PostgreSQL URL for the dedicated non-superuser role used by the "
            "sql_readonly tool. The application keeps using DATABASE_URL; the tool refuses "
            "to run without this rather than falling back to the application role. Built "
            "from the POSTGRES_READONLY_* components when unset."
        ),
    )
    POSTGRES_HOST: str | None = Field(
        default=None,
        description="Database host used when DATABASE_URL is not set. Defaults to localhost.",
    )
    POSTGRES_PORT: int | None = Field(
        default=None,
        gt=0,
        le=65535,
        description="Database port used when DATABASE_URL is not set. Defaults to 5432.",
    )
    POSTGRES_USER: str | None = Field(
        default=None,
        description="Application database role used when DATABASE_URL is not set.",
    )
    POSTGRES_PASSWORD: str | None = Field(
        default=None,
        description=(
            "Application database password used when DATABASE_URL is not set. Kept apart "
            "from the URL so any character is allowed; it is escaped during assembly."
        ),
    )
    POSTGRES_DB: str | None = Field(
        default=None,
        description="Database name used when DATABASE_URL is not set. Defaults to agentkit.",
    )
    POSTGRES_READONLY_USER: str | None = Field(
        default=None,
        description=(
            "Dedicated read-only role used when DATABASE_READONLY_URL is not set. Defaults "
            "to agentkit_readonly."
        ),
    )
    POSTGRES_READONLY_PASSWORD: str | None = Field(
        default=None,
        description="Password for the dedicated read-only role, escaped during assembly.",
    )
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL.",
    )
    REDIS_PASSWORD: str | None = Field(
        default=None,
        description=(
            "Password for Redis. Completed into REDIS_URL when the URL itself carries no "
            "credentials, so the secret can stay out of the URL string."
        ),
    )

    # LLM Settings
    LLM_PROVIDER: Literal["gemini", "openai", "fake"] = Field(
        default="gemini",
        description="LLM provider: gemini, openai, or fake.",
    )
    LLM_MODEL: str = Field(
        default="gemini-3.1-flash-lite",
        description="LLM model identifier.",
    )
    GEMINI_API_KEY: str | None = Field(
        default=None,
        description="Google Gemini API key.",
    )
    OPENAI_API_KEY: str | None = Field(
        default=None,
        description="OpenAI API key.",
    )

    # External Tools & Sandboxing
    SEARCH_API_KEY: str | None = Field(
        default=None,
        description="External search API key (Tavily/SerpAPI).",
    )
    LLM_MAX_RETRIES: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Retry attempts for transient LLM provider failures (429/5xx/network).",
    )
    LLM_RETRY_BASE_DELAY_S: float = Field(
        default=0.5,
        gt=0,
        description="Base delay in seconds for exponential LLM retry backoff.",
    )
    LLM_RETRY_MAX_DELAY_S: float = Field(
        default=10.0,
        gt=0,
        description="Maximum delay in seconds between LLM retry attempts.",
    )
    FILE_TOOL_BASE_DIR: str = Field(
        default="/tmp/agentkit_sandbox",
        description="Base directory for sandboxed file reading operations.",
    )

    # Security & API Authentication
    API_KEYS: str = Field(
        default="",
        description="Comma-separated list of valid client API keys. Must be set explicitly.",
    )
    RATE_LIMIT_PER_MIN: int = Field(
        default=30,
        gt=0,
        description="Allowed requests per minute per API key.",
    )

    # Agent Runtime Limits
    MAX_STEPS: int = Field(
        default=10,
        gt=0,
        description="Maximum execution steps allowed per agent run.",
    )
    RUN_TIMEOUT_S: int = Field(
        default=60,
        gt=0,
        description="Maximum overall execution time in seconds for a run.",
    )
    TOOL_TIMEOUT_S: int = Field(
        default=15,
        gt=0,
        description="Maximum execution time in seconds per tool execution.",
    )
    SESSION_TTL_S: int = Field(
        default=3600,
        gt=0,
        description="TTL in seconds for Redis conversation session cache.",
    )
    SESSION_MAX_MESSAGES: int = Field(
        default=40,
        gt=0,
        description=(
            "Maximum messages retained per session history (system prompt included). "
            "Truncation never leaves a tool result without its tool call."
        ),
    )
    TOOL_OUTPUT_MAX_CHARS: int = Field(
        default=2000,
        gt=0,
        description="Maximum characters allowed in tool outputs before truncation.",
    )

    @model_validator(mode="after")
    def _resolve_datastore_urls(self) -> "Settings":
        """Resolve connection URLs from components and finish them with the secrets.

        Credentials travel as separate variables because a password cannot be interpolated
        into a URL string safely: the generated values contain ``/``, ``+`` and ``=``. The
        URLs are assembled here, with each component escaped, so the API, the scripts and
        the real-infrastructure suite all use identical, valid URLs. An explicitly
        configured URL is authoritative and left untouched.

        ``REDIS_PASSWORD`` is embedded into a credential-less ``REDIS_URL`` for the same
        reason, so the secret need not be repeated inside the URL.
        """
        env: dict[str, str] = {
            "DATABASE_URL": (self.DATABASE_URL if "DATABASE_URL" in self.model_fields_set else ""),
            "DATABASE_READONLY_URL": (
                self.DATABASE_READONLY_URL
                if "DATABASE_READONLY_URL" in self.model_fields_set
                else ""
            )
            or "",
            "POSTGRES_HOST": self.POSTGRES_HOST or "",
            "POSTGRES_PORT": str(self.POSTGRES_PORT) if self.POSTGRES_PORT else "",
            "POSTGRES_USER": self.POSTGRES_USER or "",
            "POSTGRES_PASSWORD": self.POSTGRES_PASSWORD or "",
            "POSTGRES_DB": self.POSTGRES_DB or "",
            "POSTGRES_READONLY_USER": self.POSTGRES_READONLY_USER or "",
            "POSTGRES_READONLY_PASSWORD": self.POSTGRES_READONLY_PASSWORD or "",
        }
        application_url = resolve_postgres_url(
            env,
            url_var="DATABASE_URL",
            user_var="POSTGRES_USER",
            password_var="POSTGRES_PASSWORD",
            default_user=DEFAULT_POSTGRES_USER,
        )
        if application_url:
            self.DATABASE_URL = application_url
        self.DATABASE_READONLY_URL = resolve_postgres_url(
            env,
            url_var="DATABASE_READONLY_URL",
            user_var="POSTGRES_READONLY_USER",
            password_var="POSTGRES_READONLY_PASSWORD",
            default_user=DEFAULT_READONLY_USER,
        )
        self.REDIS_URL = apply_redis_password(self.REDIS_URL, self.REDIS_PASSWORD)
        return self

    @field_validator("API_KEYS")
    @classmethod
    def validate_api_keys(cls, v: str) -> str:
        """Reject blank or placeholder keys so a deployment cannot start unauthenticated.

        The default is intentionally empty: starting with a key published in the repository
        would expose any deployment that forgets to configure its own.
        """
        cleaned = v.strip()
        if not cleaned:
            raise ValueError(
                "API_KEYS must be set to a comma-separated list of client API keys. "
                "AgentKit refuses to start without explicit authentication credentials."
            )
        return cleaned

    @property
    def api_keys_list(self) -> list[str]:
        """Return parsed and stripped list of valid API keys."""
        return [k.strip() for k in self.API_KEYS.split(",") if k.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton instance of application Settings."""
    return Settings()
