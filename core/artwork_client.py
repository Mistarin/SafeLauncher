"""Artwork transport boundary.

SteamGridDBClient remains the compatibility implementation and public legacy
name. New code should depend on ArtworkClient so artwork transport can evolve
independently from Steam metadata transport.
"""

from core.steamgriddb_client import SteamGridDBClient


class ArtworkClient(SteamGridDBClient):
    """SteamGridDB/Steam artwork transport with local safety checks and cache."""


__all__ = ["ArtworkClient"]
