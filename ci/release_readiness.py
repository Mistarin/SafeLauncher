#!/usr/bin/env python3
"""Run SafeLauncher's isolated production-readiness verification gate.

The gate composes existing checks; it does not replace them or contact a
backend. Every application-facing command receives fresh XDG directories so
credentials, databases, settings, caches, and test fixtures from a developer
profile cannot affect the result.

Real backend and artwork latency must be measured separately against a
controlled staging environment. The performance command here is deliberately
the deterministic offline regression guard for local projection and request
manager overhead.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class GateStep:
    name: str
    command: tuple[str, ...]
    timeout_seconds: int


def isolated_environment(xdg_root: Path, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a deterministic test environment without exposing credentials."""
    environment = dict(base or os.environ)
    for variable in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        path = xdg_root / variable.removeprefix("XDG_").lower()
        path.mkdir(parents=True, exist_ok=True)
        environment[variable] = str(path)
    # Do not forward deployment credentials to an offline verification child
    # process. Tests provide their own fake values where needed.
    for key in tuple(environment):
        upper = key.upper()
        if upper.startswith(("CONVEX_", "VERCEL_", "SAFELAUNCHER_")) and any(
            marker in upper for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD", "PRIVATE")
        ):
            environment.pop(key, None)
    environment.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "SAFELAUNCHER_DISABLE_UPDATE_CHECK": "1",
            "SAFELAUNCHER_OFFLINE_TEST_MODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return environment


def gate_steps(
    *,
    python_executable: str = sys.executable,
    timeout_seconds: int = 300,
    smoke_timeout_seconds: int = 180,
    games: int = 600,
    requests: int = 100,
    repetitions: int = 5,
    workers: int = 3,
    max_render_ms: float = 250.0,
) -> tuple[GateStep, ...]:
    """Build the ordered, observable release-gate command list."""
    python = str(python_executable)
    return (
        GateStep("compile", (python, "-m", "compileall", "-q", "core", "ui", "tests", "ci"), timeout_seconds),
        GateStep("unit tests", (python, "-m", "unittest", "discover", "-s", "tests", "-q"), timeout_seconds),
        GateStep(
            "smoke phases",
            (python, "-u", "ci/smoke_phase.py", "all", "--timeout-seconds", str(smoke_timeout_seconds)),
            smoke_timeout_seconds * 4,
        ),
        GateStep("full harness", (python, "test.py"), timeout_seconds),
        GateStep("security audit", (python, "ci/security_audit.py"), timeout_seconds),
        GateStep("worker audit", (python, "ci/worker_audit.py"), timeout_seconds),
        GateStep(
            "offline performance guard",
            (
                python,
                "ci/performance_baseline.py",
                "--games",
                str(games),
                "--requests",
                str(requests),
                "--repetitions",
                str(repetitions),
                "--workers",
                str(workers),
                "--max-render-ms",
                str(max_render_ms),
                "--max-workers-peak",
                str(workers),
            ),
            timeout_seconds,
        ),
        GateStep("build AI manifest", (python, ".ai/tools/build_manifest.py"), timeout_seconds),
        GateStep("validate AI cache", (python, ".ai/tools/validate_cache.py"), timeout_seconds),
        GateStep("diff check", ("git", "diff", "--check"), timeout_seconds),
    )


def run_step(step: GateStep, *, environment: Mapping[str, str]) -> int:
    """Run one gate step and preserve its output in CI logs."""
    print(f"\n==> {step.name}: {' '.join(step.command)}", flush=True)
    try:
        completed = subprocess.run(
            step.command,
            cwd=ROOT,
            env=dict(environment),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=step.timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        output = error.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        if output:
            print(output, end="")
        print(f"✗ {step.name} timed out after {step.timeout_seconds}s", file=sys.stderr)
        return 124

    if completed.stdout:
        print(completed.stdout, end="")
    if completed.returncode:
        print(f"✗ {step.name} failed with exit code {completed.returncode}", file=sys.stderr)
    else:
        print(f"✓ {step.name} passed", flush=True)
    return completed.returncode


def run_gate(
    *,
    xdg_root: Path | None = None,
    environment: Mapping[str, str] | None = None,
    steps: Sequence[GateStep] | None = None,
) -> int:
    """Run the complete gate, stopping at the first failed invariant."""
    temporary_root = None
    if xdg_root is None:
        temporary_root = tempfile.TemporaryDirectory(prefix="safelauncher-release-")
        xdg_root = Path(temporary_root.name)
    try:
        prepared_environment = isolated_environment(
            xdg_root,
            os.environ if environment is None else environment,
        )
        for step in steps or gate_steps():
            status = run_step(step, environment=prepared_environment)
            if status:
                return status
        print("\n✓ SafeLauncher release-readiness gate passed", flush=True)
        return 0
    finally:
        if temporary_root is not None:
            temporary_root.cleanup()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=600, help="Synthetic library size for the offline guard")
    parser.add_argument("--requests", type=int, default=100, help="Managed request count for the offline guard")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument(
        "--max-render-ms",
        type=float,
        default=250.0,
        help="Synthetic local-render regression limit; real network timing is not measured here",
    )
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--smoke-timeout-seconds", type=int, default=180)
    args = parser.parse_args(argv)
    values = (args.games, args.requests, args.repetitions, args.workers, args.timeout_seconds, args.smoke_timeout_seconds)
    if min(values) < 1 or args.max_render_ms <= 0:
        parser.error("counts, workers, and timeouts must be positive and max-render-ms must be greater than zero")
    return run_gate(
        steps=gate_steps(
            timeout_seconds=args.timeout_seconds,
            smoke_timeout_seconds=args.smoke_timeout_seconds,
            games=args.games,
            requests=args.requests,
            repetitions=args.repetitions,
            workers=args.workers,
            max_render_ms=args.max_render_ms,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
