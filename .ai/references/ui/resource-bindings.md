# UI Reference: Resource Bindings

[`ui/resource_binding.py`](../../../ui/resource_binding.py) is the supported bridge from Qt-free manager listeners to Qt widgets. A binding has a stable key, subscribes to state, emits on the Qt event loop, and can close/unsubscribe safely.

Binding owners should:

- keep the binding alive as long as the view needs it;
- render usable fresh/stale values immediately;
- show loading only without usable data;
- close on hide/destruction/context changes;
- reject obsolete generations and game/account identities.
