# UI Reference: Profile

[`ui/components/profile_page.py`](../../../ui/components/profile_page.py) edits and publishes the curated profile, displays local/account-wide stats, and consumes profile artwork/social data. It must preserve the private/public boundary and must not display private cloud credentials or installation details as public profile data.

Remote profile reads, social operations, avatar catalog/downloads, owner publication, and Steam artwork fallback loading are delegated to [`core/profile_resource_service.py`](../../../core/profile_resource_service.py). Profile resource keys/specs are context-isolated there as well. The widget owns presentation state and short-lived compatibility workers only; RequestManager owns managed request lifecycle and ResourceCache owns reusable remote-resource caching.
