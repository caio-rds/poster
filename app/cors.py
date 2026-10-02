from app.config import Settings


def cors_options(settings: Settings) -> dict:
    return {
        "allow_origins": settings.cors_allow_origins,
        "allow_methods": ["GET", "PATCH", "OPTIONS"],
        "allow_headers": ["apikey", "Content-Type"],
        "allow_credentials": False,
        "max_age": settings.cors_max_age,
    }
