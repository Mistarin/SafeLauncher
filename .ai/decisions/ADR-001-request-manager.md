# ADR-001: Shared Request and Resource Manager

## Decision

Use one application-scoped, Qt-free `RequestManager` with a shared `ResourceCache` for managed remote resources. UI integration happens through `ResourceBinding`.

## Reason

This centralizes priority, deduplication, retries, cancellation, cache freshness, generation safety, metrics, and shutdown instead of duplicating them in feature workers.

## Consequence

Transport clients remain separate, and compatibility workers remain temporarily available for manager-less callers.
