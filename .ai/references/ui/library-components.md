# UI Reference: Library Components

Library presentation is distributed among [`ui/library_list.py`](../../../ui/library_list.py), [`ui/components/library_view_host.py`](../../../ui/components/library_view_host.py), [`ui/components/compact_game_page.py`](../../../ui/components/compact_game_page.py), [`ui/components/steam_game_page.py`](../../../ui/components/steam_game_page.py), [`ui/components/virtual_grid.py`](../../../ui/components/virtual_grid.py), [`ui/components/responsive_grid.py`](../../../ui/components/responsive_grid.py), and supporting cards/sidebar components.

Local snapshots should drive list membership/filtering. Remote resources should update presentation through stable bindings and must not accidentally reintroduce filtered or obsolete game IDs.
