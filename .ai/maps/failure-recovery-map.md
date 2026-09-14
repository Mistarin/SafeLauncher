# Failure and Recovery Map

| Failure | Preserve | Recover |
|---|---|---|
| offline startup | local DB and stale resource values | retry managed resources later |
| transient HTTP failure | stale cached value | bounded retry/backoff |
| permanent/auth failure | local state | show explicit error and require configuration |
| cloud metadata conflict | local and remote revisions | merge/retry with revision awareness |
| pending sync failure | newest local change | digest-aware queue retry |
| save conflict | both generations | explicit user choice and backup retention |
| DB corruption | backup copy | integrity restore or safe in-memory fallback |
| late worker result | newer UI generation | discard obsolete response |
| shutdown with active worker | application safety | cooperative cancellation and bounded wait |
