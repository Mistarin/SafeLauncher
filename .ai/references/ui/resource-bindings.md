# UI Reference: Resource Bindings

[`ui/resource_binding.py`](../../../ui/resource_binding.py) is the supported bridge from Qt-free manager listeners to Qt widgets. `bind_resource()` subscribes to a stable key; `bind_request()` additionally scopes delivery to one request handle. Bindings emit on the Qt event loop and can close/unsubscribe safely.

`ResourceBindingRegistry` provides the corresponding owner-scoped collection
for pages and windows. It stores binding lifetimes only; remote values,
freshness, retries, and deduplication remain in the core manager/cache.

Binding owners should:

- keep the binding alive as long as the view needs it;
- render usable fresh/stale values immediately;
- show loading only without usable data;
- close on hide/destruction/context changes;
- reject obsolete generations and game/account identities.
