# SafeLauncher desktop UI guide

SafeLauncher is organized around the local game library, with profile and cloud
management available from the main window. The exact layout adapts to the
window size and selected library view.

## Main window

- **Library sidebar:** switch between all, installed, favorite, and archived
  games; choose a collection; create or manage collections.
- **Library toolbar:** search and sort the current view. The library can use a
  responsive card grid, a virtualized grid for larger libraries, or a compact
  list. Selecting a game opens its detail inspector when that view is active.
- **Footer:** add a game and switch between the compact and grid presentations.
  Empty grid states provide an Add Game action when the current view has no
  matching games.
- **Header:** open Cloud Center, use the profile/account menu, and access the
  window controls. The profile page is a full page in the main window; friends
  and other profile actions can open dialogs.

## Add and launch games

1. Choose **Add Game** from the footer or an empty-library prompt.
2. Select the game directory, set its executable and launch mode, then save.
3. Select the game and use its launch control or double-click its card or row.
4. Use the game detail inspector for installation status, metadata, achievements,
   and per-game actions.

Archive installation and game updates are available through the library's
game-management actions. Uninstalling archives a game record; deleting all
local data is a separate confirmed action.

## Saves and cloud

- **Cloud Center** is the main entry point for private-cloud connection,
  synchronization status, devices, quota, conflicts, and save history.
- Per-game Cloud menus open the existing save-management and history workflows.
- SafeLauncher keeps local saves available if cloud services are offline. It
  asks before resolving conflicting local and cloud versions.
- Automatic uploads occur when the managed cloud lifecycle determines it is
  safe to do so. A game can also use local saves when cloud sync is unavailable.

## Profile and social features

Use the profile control in the header to open the local profile page and account
actions. The public profile is a curated view; it does not publish installation
paths, executable names, devices, credentials, or private save state. Friends
and public profiles are opened from their corresponding profile actions.

## Keyboard and accessibility

The library provides search, selection, and context-menu keyboard actions.
Interactive controls keep their normal Return, Space, and typing behavior;
library shortcuts are ignored while text fields, buttons, or other action
controls have focus. Delegate-painted badges expose tooltip and accessibility
text. Dialogs provide explicit focus order and accessible names for important
actions.

## Status and troubleshooting

Loading, cached, offline, empty, and error states are shown with text as well as
icons. Cloud and Steam checks may be unavailable in offline mode; cached local
library data remains the source for the installed game list. For technical
cloud setup, open Cloud Center and follow its connection or setup workflow.
