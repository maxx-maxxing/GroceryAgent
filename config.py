import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


def _env_int(name, default):
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return int(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class AppConfig:
    client_id: str | None
    client_secret: str | None
    redirect_uri: str | None
    token_file: str = "tokens.json"
    auth_url: str = "https://api.kroger.com/v1/connect/oauth2/authorize"
    token_url: str = "https://api.kroger.com/v1/connect/oauth2/token"
    api_base_url: str = "https://api.kroger.com/v1"
    preferred_location_id: str = "62000056"
    store_zip_code: str = "80223"
    store_radius_miles: int = 15
    store_limit: int = 10
    product_search_limit: int = 5
    add_by_term_search_limit: int = 10


def load_config():
    return AppConfig(
        client_id=os.getenv("CLIENT_ID"),
        client_secret=os.getenv("CLIENT_SECRET"),
        redirect_uri=os.getenv("REDIRECT_URI"),
        token_file=os.getenv("TOKEN_FILE", "tokens.json"),
        auth_url=os.getenv(
            "KROGER_AUTH_URL",
            "https://api.kroger.com/v1/connect/oauth2/authorize",
        ),
        token_url=os.getenv(
            "KROGER_TOKEN_URL",
            "https://api.kroger.com/v1/connect/oauth2/token",
        ),
        api_base_url=os.getenv("KROGER_API_BASE_URL", "https://api.kroger.com/v1"),
        preferred_location_id=os.getenv("PREFERRED_LOCATION_ID", "62000056"),
        store_zip_code=os.getenv("STORE_ZIP_CODE", "80223"),
        store_radius_miles=_env_int("STORE_RADIUS_MILES", 15),
        store_limit=_env_int("STORE_LIMIT", 10),
        product_search_limit=_env_int("PRODUCT_SEARCH_LIMIT", 5),
        add_by_term_search_limit=_env_int("ADD_BY_TERM_SEARCH_LIMIT", 10),
    )
