# Workflow: Application Startup

1. `main.py` interprets CLI modes and offline override.
2. Bootstrap configures environment and single-instance behavior.
3. Qt application and dependency checks run.
4. `GameDatabase` initializes or recovers the local database.
5. `MainWindow` creates the shared `RequestManager`, `ResourceCache`, transports, and feature coordinators.
6. The local library renders first.
7. Optional cloud, Steam, artwork, and achievement resources load through managed requests.

Success means the local library remains usable even if optional services are offline.
