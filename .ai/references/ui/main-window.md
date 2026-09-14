# UI Reference: Main Window

[`ui/main_window.py`](../../../ui/main_window.py) is the application composition root and long-lived UI owner. It creates the database-facing library, shared request/cache services, Steam/artwork/cloud/profile clients, view hosts, and feature lifecycle state. It also contains many compatibility fallbacks because dialogs can be constructed independently.

Before editing it, identify whether the path is local-library rendering, managed resource loading, a compatibility worker fallback, or shutdown. Update the appropriate architecture and map page when a boundary changes.
