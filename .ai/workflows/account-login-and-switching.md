# Workflow: Account Login and Switching

Account context must be resolved before submitting account-specific resource requests. When the account or backend changes:

1. Close or invalidate account-scoped UI bindings.
2. Advance the relevant request generation.
3. Rebuild local/private projection as appropriate.
4. Use context-isolated request and cache keys.
5. Reject late responses from the previous account.
6. Refresh public profile resources only through the central-auth/profile-service path.

Never reuse private cloud cache values across accounts or deployments without an explicit opaque context boundary.
