# Import and Dependency Boundaries

- Core modules should not import UI widgets except where an existing compatibility worker explicitly requires Qt.
- `core/request_manager.py` and `core/resource_cache.py` are Qt-free.
- `ui/resource_binding.py` is the Qt boundary for manager state.
- Transport clients should not import widgets or decide presentation.
- `database.py` should remain the persistence boundary rather than being called directly by remote transport code for arbitrary UI updates.
- Profile service code is separate from private cloud transport.
- Services under `services/` are deployable TypeScript applications, not Python runtime dependencies.

The generated import graph is the evidence source for actual dependencies; this page records intended boundaries.
