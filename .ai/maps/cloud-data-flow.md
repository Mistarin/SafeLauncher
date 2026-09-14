# Private Cloud Data Flow

```text
Local UI/edit/session
  → GameDatabase local mutation
  → CloudMetadataSync payload/merge
  → RequestManager (async boundary where used)
  → CloudClient
  → SafeLauncherCloud / Convex
  → revision/conflict response
  → merge/apply to SQLite
  → UI library/profile refresh
```

Failure path:

```text
offline/auth/backend failure
  → preserve local projection
  → enqueue context + operation + digest
  → retry after connectivity/configuration recovery
  → acknowledge only matching digest
```

Cloud-only game records are projected locally. Installation remains a private library state and is not sent to the public profile projection.
