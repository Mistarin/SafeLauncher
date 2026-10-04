"""Terminal-only command dispatch; importing it does not create Qt objects."""


def dispatch_command(arguments: list[str]) -> int | None:
    """Return a command's exit status, or None to start the GUI."""
    if any(arg in arguments for arg in ("--doctor", "doctor", "-d")):
        from core.system_inspector import print_system_report
        print_system_report()
        return 0
    if any(arg in arguments for arg in ("--setup-cloud", "setup-cloud", "-c")):
        from core.cloud_cli_wizard import run_cloud_setup_wizard
        return run_cloud_setup_wizard()
    if any(arg in arguments for arg in ("--install-desktop", "install-desktop", "-i")):
        from core.desktop_integration import install_safelauncher_desktop_entry
        ok, message = install_safelauncher_desktop_entry()
        color = "92" if ok else "91"
        print(f"\033[{color}m{'✔' if ok else '✖'} {message}\033[0m")
        return 0 if ok else 1
    return None
