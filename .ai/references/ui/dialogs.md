# UI Reference: Dialogs

Dialogs are under [`ui/dialogs/`](../../../ui/dialogs/). Relevant families include account/profile, achievements, game properties, cloud setup/save management, conflicts, friends, settings, Proton/runtime, and archive/game operations.

Many dialogs support manager-backed requests when embedded by `MainWindow` and retain worker fallbacks when constructed without one. Check constructor dependencies before removing a worker or assuming a manager exists.
