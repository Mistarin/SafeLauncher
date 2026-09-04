#!/usr/bin/env python3
"""
Interactive Version Management Wizard for SafeLauncher.
Allows inspecting and bumping Application Version and Convex Backend Version.
"""

from __future__ import annotations

import os
import re
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Tuple

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.version import (
    APP_VERSION,
    MIN_CONVEX_BACKEND_VERSION,
    GITHUB_REPO,
    BACKEND_GITHUB_REPO,
    parse_version,
    set_version,
)

# Terminal ANSI styling
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def _banner(title: str, color=CYAN) -> None:
    width = 62
    line = "─" * max(0, width - len(title) - 6)
    print(f"\n{color}{BOLD}┌── {title} {line}┐{RESET}")


def _footer(color=CYAN) -> None:
    print(f"{color}{BOLD}└{'─' * 60}┘{RESET}\n")


def _get_latest_git_tag() -> str:
    """Retrieve latest git tag if available."""
    if shutil.which("git"):
        try:
            res = subprocess.run(
                ["git", "describe", "--tags", "--abbrev=0"],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
    return "None"


def _suggest_bumps(ver: str) -> Tuple[str, str, str]:
    """Return (patch, minor, major) suggestions based on a semver string."""
    parts = parse_version(ver)
    while len(parts) < 3:
        parts = parts + (0,)
    major, minor, patch = parts[0], parts[1], parts[2]
    return (
        f"{major}.{minor}.{patch + 1}",
        f"{major}.{minor + 1}.0",
        f"{major + 1}.0.0",
    )


def run_version_wizard() -> int:
    """Run interactive terminal version bump wizard."""
    while True:
        # Re-import to read any recently modified versions from disk
        import importlib
        import core.version as v_mod
        importlib.reload(v_mod)

        cur_app = v_mod.APP_VERSION
        cur_backend = v_mod.MIN_CONVEX_BACKEND_VERSION
        latest_tag = _get_latest_git_tag()

        _banner("SafeLauncher Version Wizard", CYAN)
        print(f"  {BOLD}Current App Version:{RESET}       {GREEN}{BOLD}{cur_app}{RESET}")
        print(f"  {BOLD}Minimum Convex Backend:{RESET}    {YELLOW}{BOLD}{cur_backend}{RESET}")
        print(f"  {BOLD}Latest Git Tag:{RESET}            {DIM}{latest_tag}{RESET}")
        print(f"  {BOLD}Client Repository:{RESET}         {DIM}{GITHUB_REPO}{RESET}")
        print(f"  {BOLD}Backend Repository:{RESET}        {DIM}{BACKEND_GITHUB_REPO}{RESET}")
        _footer(CYAN)

        patch, minor, major = _suggest_bumps(cur_app)

        print(f"  {BOLD}Select what you would like to manage:{RESET}")
        print(f"   {CYAN}{BOLD}[1]{RESET} Change {BOLD}Application Version{RESET} (Current: {GREEN}{cur_app}{RESET})")
        print(f"   {CYAN}{BOLD}[2]{RESET} Change {BOLD}Minimum Backend Version{RESET} (Current: {YELLOW}{cur_backend}{RESET})")
        print(f"   {CYAN}{BOLD}[3]{RESET} Change {BOLD}Both{RESET} (App + Backend)")
        print(f"   {CYAN}{BOLD}[4]{RESET} Create Git Tag for current app version ({DIM}v{cur_app}{RESET})")
        print(f"   {CYAN}{BOLD}[5]{RESET} Exit")

        try:
            choice = input(f"\n  {CYAN}{BOLD}➜{RESET} Enter choice [1-5]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n  {DIM}Exiting.{RESET}\n")
            return 0

        if choice == "1":
            _banner("Change Application Version", GREEN)
            print(f"   {BOLD}[1]{RESET} Patch bump: {GREEN}{patch}{RESET} (+0.0.1)")
            print(f"   {BOLD}[2]{RESET} Minor bump: {GREEN}{minor}{RESET} (+0.1.0)")
            print(f"   {BOLD}[3]{RESET} Major bump: {GREEN}{major}{RESET} (+1.0.0)")
            print(f"   {BOLD}[4]{RESET} Custom version input")
            print(f"   {BOLD}[5]{RESET} Back to main menu")

            sub = input(f"\n  {CYAN}{BOLD}➜{RESET} Select option [1-5]: ").strip()
            new_v = ""
            if sub == "1":
                new_v = patch
            elif sub == "2":
                new_v = minor
            elif sub == "3":
                new_v = major
            elif sub == "4":
                custom = input(f"  {CYAN}{BOLD}➜{RESET} Enter new version (e.g. 0.6.0): ").strip().lstrip("vV")
                if custom and re.match(r"^\d+(\.\d+)*", custom):
                    new_v = custom
                else:
                    print(f"  {RED}✖ Invalid version format.{RESET}")
            elif sub == "5":
                continue

            if new_v:
                v_mod.set_version(new_v)
                print(f"\n  {GREEN}{BOLD}✔ Application version updated to: {new_v}{RESET}")
            _footer(GREEN)

        elif choice == "2":
            b_patch, b_minor, _ = _suggest_bumps(cur_backend)
            _banner("Change Minimum Convex Backend Version", YELLOW)
            print(f"   {BOLD}[1]{RESET} Patch bump: {YELLOW}{b_patch}{RESET} (+0.0.1)")
            print(f"   {BOLD}[2]{RESET} Minor bump: {YELLOW}{b_minor}{RESET} (+0.1.0)")
            print(f"   {BOLD}[3]{RESET} Custom version input")
            print(f"   {BOLD}[4]{RESET} Back to main menu")

            sub = input(f"\n  {CYAN}{BOLD}➜{RESET} Select option [1-4]: ").strip()
            new_b = ""
            if sub == "1":
                new_b = b_patch
            elif sub == "2":
                new_b = b_minor
            elif sub == "3":
                custom = input(f"  {CYAN}{BOLD}➜{RESET} Enter new backend version (e.g. 1.3.0): ").strip().lstrip("vV")
                if custom and re.match(r"^\d+(\.\d+)*", custom):
                    new_b = custom
                else:
                    print(f"  {RED}✖ Invalid version format.{RESET}")
            elif sub == "4":
                continue

            if new_b:
                v_mod.set_version(cur_app, new_b)
                print(f"\n  {YELLOW}{BOLD}✔ Minimum Convex backend version updated to: {new_b}{RESET}")
            _footer(YELLOW)

        elif choice == "3":
            _banner("Change Both Versions", CYAN)
            app_in = input(f"  {CYAN}{BOLD}➜{RESET} New App Version [{patch}]: ").strip().lstrip("vV") or patch
            back_in = input(f"  {CYAN}{BOLD}➜{RESET} New Minimum Backend Version [{cur_backend}]: ").strip().lstrip("vV") or cur_backend
            v_mod.set_version(app_in, back_in)
            print(f"\n  {GREEN}{BOLD}✔ Updated App to {app_in} and Backend to {back_in}{RESET}")
            _footer(CYAN)

        elif choice == "4":
            tag_name = f"v{cur_app}"
            _banner(f"Create Git Tag ({tag_name})", GREEN)
            confirm = input(f"  {YELLOW}{BOLD}➜{RESET} Create git tag '{tag_name}' now? [Y/n]: ").strip().lower()
            if confirm not in ("n", "no"):
                if shutil.which("git"):
                    try:
                        res = subprocess.run(
                            ["git", "tag", "-a", tag_name, "-m", f"Release {tag_name}"],
                            cwd=str(ROOT_DIR),
                            capture_output=True,
                            text=True,
                        )
                        if res.returncode == 0:
                            print(f"  {GREEN}{BOLD}✔ Git tag '{tag_name}' created successfully!{RESET}")
                            print(f"  {DIM}To push: git push origin {tag_name}{RESET}")
                        else:
                            print(f"  {RED}✖ Could not create tag: {res.stderr.strip()}{RESET}")
                    except Exception as e:
                        print(f"  {RED}✖ Git command failed: {e}{RESET}")
                else:
                    print(f"  {RED}✖ git binary not found on PATH.{RESET}")
            _footer(GREEN)

        elif choice in ("5", "q", "exit"):
            print(f"  {DIM}Exiting wizard.{RESET}\n")
            return 0
        else:
            print(f"  {RED}Invalid option. Please enter 1-5.{RESET}")


if __name__ == "__main__":
    sys.exit(run_version_wizard())
