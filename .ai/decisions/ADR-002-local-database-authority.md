# ADR-002: SQLite Is the Local Authority

## Decision

SQLite is authoritative for the local library projection, playtime, achievements, and session records. Remote services reconcile into it or provide explicitly remote resource state.

## Reason

The launcher must work offline, preserve local edits, and render the library before optional network services complete.

## Consequence

Cloud responses cannot directly become arbitrary widget state or silently replace newer local records.
