#!/usr/bin/env python3
"""
Interactive Version Management Wizard for SafeLauncher.
Coherently manages Client Version (core/version.py), Client Minimum Backend Requirement,
and the Backend Repository's actual code version (convex/lib/limits.ts).
"""

from __future__ import annotations

import os
import re
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

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
from core.cloud_detector import detect_local_cloud_installation
from core.cloud_backend import check_backend_health

# Terminal ANSI styling
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def _banner(title: str, color=CYAN) -> None:
    width = 68
    line = "─" * max(0, width - len(title) - 6)
    print(f"\n{color}{BOLD}┌── {title} {line}┐{RESET}")


def _footer(color=CYAN) -> None:
    print(f"{color}{BOLD}└{'─' * 66}┘{RESET}\n")


def _get_latest_git_tag() -> str:
    """Retrieve latest git tag for SafeLauncher client repo."""
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


def _get_backend_repo_info() -> Optional[Dict[str, Any]]:
    """Detect local backend repo and parse its actual BACKEND_VERSION."""
    info = detect_local_cloud_installation()
    if not info or not info.get("path"):
        return None

    backend_path = Path(info["path"])
    limits_file = backend_path / "convex" / "lib" / "limits.ts"
    backend_code_ver = "Unknown"

    if limits_file.is_file():
        try:
            text = limits_file.read_text(encoding="utf-8")
            m = re.search(r'BACKEND_VERSION\s*=\s*"([^"]+)"', text)
            if m:
                backend_code_ver = m.group(1)
        except Exception:
            pass

    return {
        "path": backend_path,
        "limits_file": limits_file if limits_file.is_file() else None,
        "code_version": backend_code_ver,
        "site_url": info.get("site_url", ""),
    }


def _update_backend_code_version(limits_file: Path, new_version: str) -> bool:
    """Update BACKEND_VERSION in convex/lib/limits.ts and package.json."""
    clean_v = new_version.strip().lstrip("vV")
    updated = False
    try:
        text = limits_file.read_text(encoding="utf-8")
        new_text = re.sub(r'BACKEND_VERSION\s*=\s*"[^"]+"', f'BACKEND_VERSION = "{clean_v}"', text)
        limits_file.write_text(new_text, encoding="utf-8")
        updated = True

        pkg_json = limits_file.parent.parent.parent / "package.json"
        if pkg_json.is_file():
            pkg_text = pkg_json.read_text(encoding="utf-8")
            pkg_text = re.sub(r'"version":\s*"[^"]+"', f'"version": "{clean_v}"', pkg_text)
            pkg_json.write_text(pkg_text, encoding="utf-8")
    except Exception as e:
        print(f"  {RED}✖ Failed to update backend code files: {e}{RESET}")
        return False
    return updated


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
    """Run interactive unified version bump wizard."""
    while True:
        import importlib
        import core.version as v_mod
        importlib.reload(v_mod)

        cur_app = v_mod.APP_VERSION
        cur_min_backend = v_mod.MIN_CONVEX_BACKEND_VERSION
        latest_tag = _get_latest_git_tag()
        backend_info = _get_backend_repo_info()

        _banner("SafeLauncher Full-Stack Version Wizard", CYAN)
        print(f"  {BOLD}Client App Version:{RESET}            {GREEN}{BOLD}{cur_app}{RESET}  (Git tag: {DIM}{latest_tag}{RESET})")
        print(f"  {BOLD}Client Min Backend Required:{RESET}   {YELLOW}{BOLD}{cur_min_backend}{RESET}")

        if backend_info:
            backend_code_ver = backend_info["code_version"]
            backend_name = backend_info["path"].name
            print(f"  {BOLD}Backend Repo Code Version:{RESET}     {CYAN}{BOLD}{backend_code_ver}{RESET}  ({DIM}{backend_name}/convex/lib/limits.ts{RESET})")
            if backend_info["site_url"]:
                # Fast ping
                h = check_backend_health(backend_info["site_url"], timeout=1.5)
                if h.get("healthy"):
                    live_ver = h.get("version", "Unknown")
                    live_color = GREEN if live_ver == backend_code_ver else YELLOW
                    print(f"  {BOLD}Live Deployed Backend:{RESET}         {live_color}{BOLD}{live_ver}{RESET}  ({DIM}{h.get('latency_ms', 0)}ms{RESET})")
                else:
                    print(f"  {BOLD}Live Deployed Backend:{RESET}         {RED}Unreachable{RESET} ({backend_info['site_url']})")
        else:
            print(f"  {BOLD}Backend Repo:{RESET}                  {DIM}No local SafeLauncherCloud directory detected{RESET}")

        _footer(CYAN)

        patch_app, minor_app, major_app = _suggest_bumps(cur_app)

        print(f"  {BOLD}Select an action:{RESET}")
        print(f"   {CYAN}{BOLD}[1]{RESET} Bump {BOLD}Application Version{RESET} (Current: {GREEN}{cur_app}{RESET})")
        print(f"   {CYAN}{BOLD}[2]{RESET} Bump {BOLD}Backend Version{RESET} (Repo code + Client minimum)")
        print(f"   {CYAN}{BOLD}[3]{RESET} Bump {BOLD}Everything{RESET} (App + Backend code + Minimum)")
        print(f"   {CYAN}{BOLD}[4]{RESET} Create Git Tag for current app version ({DIM}v{cur_app}{RESET})")
        if backend_info and shutil.which("npx"):
            print(f"   {CYAN}{BOLD}[5]{RESET} Deploy Backend now ({DIM}npx convex deploy{RESET})")
            print(f"   {CYAN}{BOLD}[6]{RESET} Exit")
            max_choice = 6
        else:
            print(f"   {CYAN}{BOLD}[5]{RESET} Exit")
            max_choice = 5

        try:
            choice = input(f"\n  {CYAN}{BOLD}➜{RESET} Enter choice [1-{max_choice}]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n  {DIM}Exiting.{RESET}\n")
            return 0

        if choice == "1":
            _banner("Bump Application Version", GREEN)
            print(f"   {BOLD}[1]{RESET} Patch bump: {GREEN}{patch_app}{RESET} (+0.0.1)")
            print(f"   {BOLD}[2]{RESET} Minor bump: {GREEN}{minor_app}{RESET} (+0.1.0)")
            print(f"   {BOLD}[3]{RESET} Major bump: {GREEN}{major_app}{RESET} (+1.0.0)")
            print(f"   {BOLD}[4]{RESET} Custom version input")
            print(f"   {BOLD}[5]{RESET} Back")

            sub = input(f"\n  {CYAN}{BOLD}➜{RESET} Select option [1-5]: ").strip()
            new_v = ""
            if sub == "1":
                new_v = patch_app
            elif sub == "2":
                new_v = minor_app
            elif sub == "3":
                new_v = major_app
            elif sub == "4":
                custom = input(f"  {CYAN}{BOLD}➜{RESET} Enter new version: ").strip().lstrip("vV")
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
            _banner("Bump Backend Version (Code & Client Requirement)", YELLOW)
            ref_backend = backend_info["code_version"] if backend_info and backend_info["code_version"] != "Unknown" else cur_min_backend
            b_patch, b_minor, b_major = _suggest_bumps(ref_backend)

            print(f"   {BOLD}[1]{RESET} Patch bump: {YELLOW}{b_patch}{RESET} (+0.0.1)")
            print(f"   {BOLD}[2]{RESET} Minor bump: {YELLOW}{b_minor}{RESET} (+0.1.0)")
            print(f"   {BOLD}[3]{RESET} Major bump: {YELLOW}{b_major}{RESET} (+1.0.0)")
            print(f"   {BOLD}[4]{RESET} Custom version input")
            print(f"   {BOLD}[5]{RESET} Back")

            sub = input(f"\n  {CYAN}{BOLD}➜{RESET} Select option [1-5]: ").strip()
            new_b = ""
            if sub == "1":
                new_b = b_patch
            elif sub == "2":
                new_b = b_minor
            elif sub == "3":
                new_b = b_major
            elif sub == "4":
                custom = input(f"  {CYAN}{BOLD}➜{RESET} Enter new backend version: ").strip().lstrip("vV")
                if custom and re.match(r"^\d+(\.\d+)*", custom):
                    new_b = custom
                else:
                    print(f"  {RED}✖ Invalid version format.{RESET}")
            elif sub == "5":
                continue

            if new_b:
                # 1. Update client requirement
                v_mod.set_version(cur_app, new_b)
                print(f"  {GREEN}{BOLD}✔ Updated client MIN_CONVEX_BACKEND_VERSION to: {new_b}{RESET}")

                # 2. Update backend repo code if present
                if backend_info and backend_info.get("limits_file"):
                    if _update_backend_code_version(backend_info["limits_file"], new_b):
                        print(f"  {GREEN}{BOLD}✔ Updated backend repo BACKEND_VERSION in {backend_info['limits_file'].name} to: {new_b}{RESET}")

                # 3. Prompt for deploy if npm/npx available
                if backend_info and shutil.which("npx"):
                    do_deploy = input(f"\n  {CYAN}{BOLD}➜{RESET} Deploy updated backend now with 'npx convex deploy'? [y/N]: ").strip().lower()
                    if do_deploy in ("y", "yes"):
                        from core.host_process import host_process_env
                        print(f"  {DIM}Deploying in {backend_info['path']}...{RESET}")
                        subprocess.run(["npx", "convex", "deploy"], cwd=str(backend_info["path"]), env=host_process_env())
            _footer(YELLOW)

        elif choice == "3":
            _banner("Bump Everything (App + Backend Code + Minimum)", CYAN)
            app_in = input(f"  {CYAN}{BOLD}➜{RESET} New App Version [{patch_app}]: ").strip().lstrip("vV") or patch_app
            ref_b = backend_info["code_version"] if backend_info and backend_info["code_version"] != "Unknown" else cur_min_backend
            b_patch, _, _ = _suggest_bumps(ref_b)
            back_in = input(f"  {CYAN}{BOLD}➜{RESET} New Backend Version [{b_patch}]: ").strip().lstrip("vV") or b_patch

            v_mod.set_version(app_in, back_in)
            print(f"  {GREEN}{BOLD}✔ Updated SafeLauncher to {app_in} and client min backend to {back_in}{RESET}")

            if backend_info and backend_info.get("limits_file"):
                if _update_backend_code_version(backend_info["limits_file"], back_in):
                    print(f"  {GREEN}{BOLD}✔ Updated backend repo BACKEND_VERSION to {back_in}{RESET}")

            _footer(CYAN)

        elif choice == "4":
            tag_name = f"v{cur_app}"
            _banner(f"Create Git Tag ({tag_name})", GREEN)
            confirm = input(f"  {YELLOW}{BOLD}➜{RESET} Create git tag '{tag_name}' for SafeLauncher? [Y/n]: ").strip().lower()
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
                            print(f"  {DIM}Push with: git push origin {tag_name}{RESET}")
                        else:
                            print(f"  {RED}✖ Could not create tag: {res.stderr.strip()}{RESET}")
                    except Exception as e:
                        print(f"  {RED}✖ Git command failed: {e}{RESET}")
                else:
                    print(f"  {RED}✖ git binary not found on PATH.{RESET}")
            _footer(GREEN)

        elif choice == "5" and max_choice == 6:
            if backend_info:
                from core.host_process import host_process_env
                _banner("Deploying Convex Backend", CYAN)
                print(f"  Running 'npx convex deploy' in {backend_info['path']}...\n")
                subprocess.run(["npx", "convex", "deploy"], cwd=str(backend_info["path"]), env=host_process_env())
                _footer(CYAN)

        elif choice in (str(max_choice), "q", "exit"):
            print(f"  {DIM}Exiting version wizard.{RESET}\n")
            return 0
        else:
            print(f"  {RED}Invalid option.{RESET}")


if __name__ == "__main__":
    sys.exit(run_version_wizard())
