"""Single persistence entry point for resolver snapshots."""

from __future__ import annotations


def persist_resolution(db, game_id: int, app_id: str, resolution) -> int:
    """Save schema and provenance-aware state in the safe order.

    Schema comes first so known names become visible immediately. The DB then
    quarantines anything the schema does not recognize, allowing a later
    schema refresh to promote it without ever inflating achievement counts.
    """
    if resolution is None:
        return 0
    if resolution.schema:
        db.save_achievement_schema(game_id, app_id, resolution.schema)
    state = getattr(resolution, "state", {}) or {}
    pending = getattr(resolution, "pending_state", {}) or {}
    if not state and not pending:
        return 0
    base_provenance = getattr(resolution, "state_provenance", "local_emulator") or "local_emulator"
    # ``mixed`` is a resolution summary, not a storable source value. Per-key
    # provenance below carries the verified Steam/local distinction.
    if base_provenance == "mixed":
        base_provenance = "local_emulator"
    return db.record_achievement_state(
        game_id,
        app_id,
        state,
        pending=pending,
        provenance=base_provenance,
        verified=bool(getattr(resolution, "state_verified", False)),
        source_format=getattr(resolution, "state_format", "") or "",
        source_path=str(getattr(resolution, "state_path", "") or ""),
        provenance_by_name=getattr(resolution, "provenance_by_name", None),
        verified_by_name=getattr(resolution, "verified_by_name", None),
    )
