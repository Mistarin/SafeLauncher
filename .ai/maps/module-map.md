# Module Map

```text
main.py
 ├─ core/bootstrap.py
 ├─ database.py
 ├─ core/firejail_runner.py
 └─ ui/main_window.py
      ├─ core/library_controller.py / library_state.py
      ├─ core/request_contracts.py → request_manager.py → resource_cache.py
      ├─ core/cloud_client.py → cloud_backend.py
      ├─ core/steam_client.py
      ├─ core/artwork_client.py → steamgriddb_client.py
      ├─ core/profile_service.py → central_auth.py
      ├─ cloud/achievement/save coordinators
      ├─ ui/components/*
      └─ ui/dialogs/*
```

Use [`generated/imports.json`](../generated/imports.json) for the complete import graph and [`generated/symbols.json`](../generated/symbols.json) for symbol lookup.
