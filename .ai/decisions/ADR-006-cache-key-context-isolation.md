# ADR-006: Context Is Part of Remote Resource Identity

## Decision

Account/backend context belongs in request identities whenever a value differs by account, deployment, or credentials.

## Consequence

Cache reuse is safe across views and devices without leaking one account's private resource into another account's UI. Raw secrets are never used as key material.
