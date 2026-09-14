# Signals and Callback Flow

```text
RequestManager listener (worker thread)
  → ResourceBinding._receive_state
  → queued Qt signal
  → ResourceBinding._deliver_state
  → widget callback/render
```

Compatibility path:

```text
SafeQThread worker
  → Qt signal
  → MainWindow/dialog slot
  → database/view refresh
```

Never update a widget directly from a Qt-free manager listener. Close bindings before their owners are destroyed. Use request generations when navigation, account context, or selected game changes.

The generated connection index records discovered Qt signals, manager subscriptions, and common callback sites.
