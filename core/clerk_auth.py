"""
Deprecated: Clerk authentication has been removed from SafeLauncher.
SafeLauncher uses private personal Convex instances configured via site URL and secret key.
"""

class AuthError(Exception):
    """Deprecated authentication failure."""
    pass


def get_status() -> dict:
    return {"signed_in": False}


def clear_stored_session() -> None:
    pass


def get_access_token() -> str:
    raise AuthError("Clerk authentication has been removed.")


