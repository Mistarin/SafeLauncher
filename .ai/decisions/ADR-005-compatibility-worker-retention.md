# ADR-005: Retain Compatibility Workers During Migration

## Decision

Keep old QThread/fetcher paths where standalone dialogs, plugins, tests, or older integrations still require them. Production MainWindow paths should use managed requests where migrated.

## Consequence

There are two intentional paths for some features. The compatibility path must not grow a second global scheduling policy, and removal requires usage verification.
