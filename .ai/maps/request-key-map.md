# Request Key Map

`RequestKey(resource, identity, variant)` is the deduplication and cache identity. Identity must include account/backend context when responses vary by account or deployment.

| Resource | Identity guidance | Typical priority |
|---|---|---|
| `steam-build` | Steam AppID | normal |
| `steam-tags` | canonical game/AppID identity | background/normal |
| `artwork-cover` | AppID/name plus artwork variant | visible |
| `artwork-hero` | AppID/name plus hero variant | visible/selected |
| `profile-artwork` | canonical URL or asset identity | normal |
| `cloud-save-status` | opaque cloud context + game identity | normal |
| private profile/library | opaque account/backend context | critical/normal |
| achievement schema | AppID plus source/context | normal/background |

Do not use widget IDs, object addresses, transient callbacks, raw credentials, or local absolute paths as shared remote identities.
