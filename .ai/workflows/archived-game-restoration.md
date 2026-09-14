# Workflow: Archived or Not-Installed Game Restoration

1. A cloud/library record is materialized locally with stable profile identity.
2. The UI displays account-wide stats independently of local installability.
3. User chooses restore/install or library-only action.
4. Archive installer/extractor validates and stages files when installing.
5. Existing database identity is updated with path/executable/runtime fields.
6. `is_archived`/not-installed state is cleared for the local device.
7. Existing playtime, achievements, favorites, and last-played data remain attached.
8. Artwork and optional metadata refresh through managed resources.

The install transition must not reset account-wide history or create a duplicate record.
