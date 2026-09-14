# Workflow: Install After Cloud Sync

When one device has a game installed and another does not, both devices can share private account-wide library statistics. The uninstalled device shows the record as unavailable/not installed; the installed device shows it as playable. On installation, reconciliation matches the stable identity and loads the existing stats from SQLite/private sync rather than starting an empty record.

The public profile remains unaffected by local installation status unless that product boundary is explicitly changed later.
