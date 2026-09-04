#!/usr/bin/env python3
"""
Interactive Full-Stack Version & Release Wizard for SafeLauncher.
Coherently manages:
1. Client Version (core/version.py)
2. Client Minimum Backend Requirement (core/version.py)
3. Backend Repo Code Version (convex/lib/limits.ts)
4. Git Staging, Commit, Push, Tag, and Release Pipelines
5. Convex Backend Cloud Deployments
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
from core.cloud_backend import check_backend_health, get_site_url

# Terminal ANSI styling
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def _banner(title: str, color=CYAN) -> None:
    width = 72
    line = "-" * max(0, width - len(title) - 6)
    print(f"\n{color}{BOLD}[-- {title} {line}]{RESET}")


def _footer(color=CYAN) -> None:
    print(f"{color}{BOLD}[{'-' * 70}]{RESET}\n")


def _get_current_git_branch() -> str:
    """Return the active git branch name."""
    if shutil.which("git"):
        try:
            res = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
    return "master"


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


def _run_git_cmd(args: list[str], description: str) -> bool:
    """Execute a git command with styled output logging."""
    print(f"  {DIM}> git {' '.join(args)}{RESET}")
    try:
        res = subprocess.run(
            ["git", *args],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            if res.stdout.strip():
                for line in res.stdout.strip().splitlines():
                    print(f"    {line}")
            return True
        else:
            err = res.stderr.strip() or res.stdout.strip()
            print(f"  {RED}[x] Failed {description}: {err}{RESET}")
            return False
    except Exception as e:
        print(f"  {RED}[x] Git command exception: {e}{RESET}")
        return False


def _git_status_preview() -> Tuple[bool, list[str]]:
    """Check working tree status. Returns (has_changes, status_lines)."""
    if not shutil.which("git"):
        return False, []
    try:
        res = subprocess.run(
            ["git", "status", "--short"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=3,
        )
        lines = [line for line in res.stdout.splitlines() if line.strip()]
        return bool(lines), lines
    except Exception:
        return False, []


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
        "dev_site_url": info.get("site_url", ""),
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
        print(f"  {RED}[x] Failed to update backend code files: {e}{RESET}")
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
    """Run interactive unified version bump and git release wizard."""
    while True:
        import importlib
        import core.version as v_mod
        importlib.reload(v_mod)

        cur_app = v_mod.APP_VERSION
        cur_min_backend = v_mod.MIN_CONVEX_BACKEND_VERSION
        latest_tag = _get_latest_git_tag()
        cur_branch = _get_current_git_branch()
        backend_info = _get_backend_repo_info()
        active_site_url = get_site_url()

        _banner("SafeLauncher Full-Stack Version & Release Wizard", CYAN)
        print(f"  {BOLD}Client App Version:{RESET}            {GREEN}{BOLD}{cur_app}{RESET}  (Branch: {CYAN}{cur_branch}{RESET}, Tag: {DIM}{latest_tag}{RESET})")
        print(f"  {BOLD}Client Min Backend Required:{RESET}   {YELLOW}{BOLD}{cur_min_backend}{RESET}")

        if backend_info:
            backend_code_ver = backend_info["code_version"]
            backend_name = backend_info["path"].name
            print(f"  {BOLD}Backend Repo Code Version:{RESET}     {CYAN}{BOLD}{backend_code_ver}{RESET}  ({DIM}{backend_name}/convex/lib/limits.ts{RESET})")
        else:
            print(f"  {BOLD}Backend Repo:{RESET}                  {DIM}No local SafeLauncherCloud directory detected{RESET}")

        # Active Live Backend Probe
        if active_site_url:
            h = check_backend_health(active_site_url, timeout=2.0)
            if h.get("healthy"):
                live_ver = h.get("version", "Unknown")
                backend_code_ver = backend_info["code_version"] if backend_info else cur_min_backend
                live_color = GREEN if live_ver == backend_code_ver else YELLOW
                short_url = active_site_url.replace("https://", "").split(".convex")[0]
                status_note = "Up to date" if not h.get("is_outdated") else f"Outdated < {cur_min_backend}"
                print(f"  {BOLD}Live Active Backend (Prod):{RESET}   {live_color}{BOLD}{live_ver}{RESET}  ({DIM}{h.get('latency_ms', 0)}ms, {status_note}, {short_url}{RESET})")
            else:
                print(f"  {BOLD}Live Active Backend:{RESET}          {RED}Unreachable{RESET} ({active_site_url})")

        # Dev Deployment status if distinct
        if backend_info and backend_info.get("dev_site_url") and backend_info["dev_site_url"] != active_site_url:
            dev_url = backend_info["dev_site_url"]
            short_dev = dev_url.replace("https://", "").split(".convex")[0]
            dev_h = check_backend_health(dev_url, timeout=1.5)
            dev_ver = dev_h.get("version", "1.0.0") if dev_h.get("healthy") else "Offline"
            print(f"  {BOLD}Dev Deployment (.env.local):{RESET}  {DIM}{dev_ver}  ({short_dev}, run 'npx convex dev --once' to update){RESET}")

        _footer(CYAN)

        patch_app, minor_app, major_app = _suggest_bumps(cur_app)

        print(f"  {BOLD}Version Bumps:{RESET}")
        print(f"   {CYAN}{BOLD}[1]{RESET} Bump {BOLD}Application Version{RESET} (Current: {GREEN}{cur_app}{RESET})")
        print(f"   {CYAN}{BOLD}[2]{RESET} Bump {BOLD}Backend Version{RESET} (Repo code + Client minimum)")
        print(f"   {CYAN}{BOLD}[3]{RESET} Bump {BOLD}Both{RESET} (App + Backend code + Minimum)")

        print(f"\n  {BOLD}Git & Release Automation:{RESET}")
        print(f"   {GREEN}{BOLD}[4]{RESET} [Release] {BOLD}All-in-One Pipeline{RESET} (Bump + Add + Commit + Push + Tag + Push Tag)")
        print(f"   {CYAN}{BOLD}[5]{RESET} [Git]     {BOLD}Commit & Push{RESET} (Stage changes, commit, and push to origin/{cur_branch})")
        print(f"   {CYAN}{BOLD}[6]{RESET} [Git]     {BOLD}Create & Push Tag{RESET} (Tag v{cur_app} and push to origin)")

        print(f"\n  {BOLD}Deployment & Actions:{RESET}")
        if backend_info and shutil.which("npx"):
            print(f"   {CYAN}{BOLD}[7]{RESET} [Convex]  Deploy Backend to Production ({DIM}npx convex deploy{RESET})")
            print(f"   {CYAN}{BOLD}[8]{RESET} Exit")
            max_choice = 8
        else:
            print(f"   {CYAN}{BOLD}[7]{RESET} Exit")
            max_choice = 7

        try:
            choice = input(f"\n  {CYAN}{BOLD}>{RESET} Enter choice [1-{max_choice}]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n  {DIM}Exiting.{RESET}\n")
            return 0

        # Option 1: Bump App Version
        if choice == "1":
            _banner("Bump Application Version", GREEN)
            print(f"   {BOLD}[1]{RESET} Patch bump: {GREEN}{patch_app}{RESET} (+0.0.1)")
            print(f"   {BOLD}[2]{RESET} Minor bump: {GREEN}{minor_app}{RESET} (+0.1.0)")
            print(f"   {BOLD}[3]{RESET} Major bump: {GREEN}{major_app}{RESET} (+1.0.0)")
            print(f"   {BOLD}[4]{RESET} Custom version input")
            print(f"   {BOLD}[5]{RESET} Back")

            sub = input(f"\n  {CYAN}{BOLD}>{RESET} Select option [1-5]: ").strip()
            new_v = ""
            if sub == "1":
                new_v = patch_app
            elif sub == "2":
                new_v = minor_app
            elif sub == "3":
                new_v = major_app
            elif sub == "4":
                custom = input(f"  {CYAN}{BOLD}>{RESET} Enter new version: ").strip().lstrip("vV")
                if custom and re.match(r"^\d+(\.\d+)*", custom):
                    new_v = custom
                else:
                    print(f"  {RED}[x] Invalid version format.{RESET}")
            elif sub == "5":
                continue

            if new_v:
                v_mod.set_version(new_v)
                print(f"\n  {GREEN}{BOLD}[ok] Application version updated to: {new_v}{RESET}")
            _footer(GREEN)

        # Option 2: Bump Backend Version
        elif choice == "2":
            _banner("Bump Backend Version (Code & Client Requirement)", YELLOW)
            ref_backend = backend_info["code_version"] if backend_info and backend_info["code_version"] != "Unknown" else cur_min_backend
            b_patch, b_minor, b_major = _suggest_bumps(ref_backend)

            print(f"   {BOLD}[1]{RESET} Patch bump: {YELLOW}{b_patch}{RESET} (+0.0.1)")
            print(f"   {BOLD}[2]{RESET} Minor bump: {YELLOW}{b_minor}{RESET} (+0.1.0)")
            print(f"   {BOLD}[3]{RESET} Major bump: {YELLOW}{b_major}{RESET} (+1.0.0)")
            print(f"   {BOLD}[4]{RESET} Custom version input")
            print(f"   {BOLD}[5]{RESET} Back")

            sub = input(f"\n  {CYAN}{BOLD}>{RESET} Select option [1-5]: ").strip()
            new_b = ""
            if sub == "1":
                new_b = b_patch
            elif sub == "2":
                new_b = b_minor
            elif sub == "3":
                new_b = b_major
            elif sub == "4":
                custom = input(f"  {CYAN}{BOLD}>{RESET} Enter new backend version: ").strip().lstrip("vV")
                if custom and re.match(r"^\d+(\.\d+)*", custom):
                    new_b = custom
                else:
                    print(f"  {RED}[x] Invalid version format.{RESET}")
            elif sub == "5":
                continue

            if new_b:
                v_mod.set_version(cur_app, new_b)
                print(f"  {GREEN}{BOLD}[ok] Updated client MIN_CONVEX_BACKEND_VERSION to: {new_b}{RESET}")

                if backend_info and backend_info.get("limits_file"):
                    if _update_backend_code_version(backend_info["limits_file"], new_b):
                        print(f"  {GREEN}{BOLD}[ok] Updated backend repo BACKEND_VERSION in {backend_info['limits_file'].name} to: {new_b}{RESET}")

                if backend_info and shutil.which("npx"):
                    do_deploy = input(f"\n  {CYAN}{BOLD}>{RESET} Deploy updated backend now with 'npx convex deploy'? [y/N]: ").strip().lower()
                    if do_deploy in ("y", "yes"):
                        from core.host_process import host_process_env
                        print(f"  {DIM}Deploying in {backend_info['path']}...{RESET}")
                        subprocess.run(["npx", "convex", "deploy"], cwd=str(backend_info["path"]), env=host_process_env())
            _footer(YELLOW)

        # Option 3: Bump Both
        elif choice == "3":
            _banner("Bump Everything (App + Backend Code + Minimum)", CYAN)
            app_in = input(f"  {CYAN}{BOLD}>{RESET} New App Version [{patch_app}]: ").strip().lstrip("vV") or patch_app
            ref_b = backend_info["code_version"] if backend_info and backend_info["code_version"] != "Unknown" else cur_min_backend
            b_patch, _, _ = _suggest_bumps(ref_b)
            back_in = input(f"  {CYAN}{BOLD}>{RESET} New Backend Version [{b_patch}]: ").strip().lstrip("vV") or b_patch

            v_mod.set_version(app_in, back_in)
            print(f"  {GREEN}{BOLD}[ok] Updated SafeLauncher to {app_in} and client min backend to {back_in}{RESET}")

            if backend_info and backend_info.get("limits_file"):
                if _update_backend_code_version(backend_info["limits_file"], back_in):
                    print(f"  {GREEN}{BOLD}[ok] Updated backend repo BACKEND_VERSION to {back_in}{RESET}")

            _footer(CYAN)

        # Option 4: All-in-One Release Pipeline
        elif choice == "4":
            _banner("All-in-One Release Pipeline", GREEN)
            print(f"  This will execute the complete release flow in order:")
            print(f"   {DIM}1. Bump Version in core/version.py{RESET}")
            print(f"   {DIM}2. Stage all modifications (git add .){RESET}")
            print(f"   {DIM}3. Create Release Commit (git commit){RESET}")
            print(f"   {DIM}4. Push Branch (git push origin {cur_branch}){RESET}")
            print(f"   {DIM}5. Create Annotated Release Tag (git tag -a vX.Y.Z){RESET}")
            print(f"   {DIM}6. Push Tag to Remote (git push origin vX.Y.Z){RESET}\n")

            target_v = input(f"  {CYAN}{BOLD}>{RESET} Release version [{patch_app}]: ").strip().lstrip("vV") or patch_app
            tag_name = f"v{target_v}"

            default_msg = f"release: {tag_name}"
            commit_msg = input(f"  {CYAN}{BOLD}>{RESET} Commit message [{default_msg}]: ").strip() or default_msg

            proceed = input(f"\n  {YELLOW}{BOLD}>{RESET} Ready to execute release {BOLD}{tag_name}{RESET} on {CYAN}{cur_branch}{RESET}? [Y/n]: ").strip().lower()
            if proceed in ("n", "no"):
                print(f"  {DIM}Pipeline aborted.{RESET}")
                _footer(GREEN)
                continue

            print(f"\n  {BOLD}[Step 1/6]{RESET} Setting version in core/version.py to {target_v}...")
            v_mod.set_version(target_v)

            print(f"\n  {BOLD}[Step 2/6]{RESET} Staging files (git add .)...")
            if not _run_git_cmd(["add", "."], "git add"):
                _footer(RED)
                continue

            print(f"\n  {BOLD}[Step 3/6]{RESET} Creating commit: '{commit_msg}'...")
            if not _run_git_cmd(["commit", "-m", commit_msg], "git commit"):
                print(f"  {YELLOW}[!] Continuing to tag check if working tree had no new diffs.{RESET}")

            print(f"\n  {BOLD}[Step 4/6]{RESET} Pushing {cur_branch} to origin...")
            if not _run_git_cmd(["push", "origin", cur_branch], "git push origin branch"):
                _footer(RED)
                continue

            print(f"\n  {BOLD}[Step 5/6]{RESET} Creating annotated tag {tag_name}...")
            _run_git_cmd(["tag", "-a", tag_name, "-m", f"Release {tag_name}"], "git tag")

            print(f"\n  {BOLD}[Step 6/6]{RESET} Pushing tag {tag_name} to origin...")
            if _run_git_cmd(["push", "origin", tag_name], "git push origin tag"):
                print(f"\n  {GREEN}{BOLD}[ok] Full Release Pipeline Succeeded!{RESET}")
                print(f"  {GREEN}SafeLauncher {tag_name} has been committed, tagged, and pushed to GitHub.{RESET}")
            else:
                print(f"\n  {YELLOW}[!] Tag was created locally but could not be pushed.{RESET}")

            _footer(GREEN)

        # Option 5: Git Commit & Push
        elif choice == "5":
            _banner(f"Git Commit & Push (Branch: {cur_branch})", CYAN)
            has_changes, status_lines = _git_status_preview()

            if not has_changes:
                print(f"  {GREEN}[ok] Working tree is clean. Nothing to commit.{RESET}")
                do_push_anyway = input(f"\n  {CYAN}{BOLD}>{RESET} Push unpushed local commits to origin/{cur_branch}? [Y/n]: ").strip().lower()
                if do_push_anyway not in ("n", "no"):
                    _run_git_cmd(["push", "origin", cur_branch], "git push")
                _footer(CYAN)
                continue

            print(f"  {BOLD}Changes detected:{RESET}")
            for line in status_lines[:15]:
                print(f"    {DIM}{line}{RESET}")
            if len(status_lines) > 15:
                print(f"    {DIM}... and {len(status_lines) - 15} more files{RESET}")

            commit_msg = input(f"\n  {CYAN}{BOLD}>{RESET} Enter commit message: ").strip()
            if not commit_msg:
                print(f"  {RED}[x] Commit message cannot be empty.{RESET}")
                _footer(CYAN)
                continue

            print(f"\n  Staging files (git add .)...")
            if _run_git_cmd(["add", "."], "git add"):
                print(f"  Committing changes...")
                if _run_git_cmd(["commit", "-m", commit_msg], "git commit"):
                    push_confirm = input(f"\n  {YELLOW}{BOLD}>{RESET} Push commit to origin/{cur_branch} now? [Y/n]: ").strip().lower()
                    if push_confirm not in ("n", "no"):
                        _run_git_cmd(["push", "origin", cur_branch], "git push")
                        print(f"\n  {GREEN}{BOLD}[ok] Committed and pushed to origin/{cur_branch}!{RESET}")

            _footer(CYAN)

        # Option 6: Create & Push Git Tag
        elif choice == "6":
            default_tag = f"v{cur_app}"
            _banner(f"Create & Push Git Tag", GREEN)
            tag_name = input(f"  {CYAN}{BOLD}>{RESET} Tag name [{default_tag}]: ").strip() or default_tag
            if not tag_name.startswith("v"):
                tag_name = f"v{tag_name}"

            tag_msg = input(f"  {CYAN}{BOLD}>{RESET} Tag message [Release {tag_name}]: ").strip() or f"Release {tag_name}"

            if _run_git_cmd(["tag", "-a", tag_name, "-m", tag_msg], "git tag"):
                print(f"  {GREEN}{BOLD}[ok] Tag '{tag_name}' created locally.{RESET}")
                push_tag = input(f"\n  {YELLOW}{BOLD}>{RESET} Push tag '{tag_name}' to origin now? [Y/n]: ").strip().lower()
                if push_tag not in ("n", "no"):
                    if _run_git_cmd(["push", "origin", tag_name], "git push origin tag"):
                        print(f"\n  {GREEN}{BOLD}[ok] Tag '{tag_name}' successfully pushed to GitHub!{RESET}")
            _footer(GREEN)

        # Option 7: Deploy Backend
        elif choice == "7" and max_choice == 8:
            if backend_info:
                from core.host_process import host_process_env
                _banner("Deploying Convex Backend to Production", CYAN)
                print(f"  Running 'npx convex deploy' in {backend_info['path']}...\n")
                subprocess.run(["npx", "convex", "deploy"], cwd=str(backend_info["path"]), env=host_process_env())
                _footer(CYAN)

        # Exit
        elif choice in (str(max_choice), "q", "exit"):
            print(f"  {DIM}Exiting wizard.{RESET}\n")
            return 0
        else:
            print(f"  {RED}Invalid option. Please choose 1-{max_choice}.{RESET}")


if __name__ == "__main__":
    sys.exit(run_version_wizard())
