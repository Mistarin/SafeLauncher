# Workflow: Application Shutdown

1. Stop accepting new navigation/resource work.
2. Close resource bindings and transient dialogs.
3. Stop watchers and compatibility workers.
4. Finalize active game/session state.
5. Close profile, cloud, Steam, artwork, and other clients.
6. Log manager and performance metrics.
7. Shut down the shared request manager with bounded waiting.
8. Allow Qt object destruction.

Do not use unowned background objects that can emit into deleted Qt receivers.
