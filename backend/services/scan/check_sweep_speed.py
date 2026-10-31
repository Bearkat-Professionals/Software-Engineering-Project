"""Developer tool (#81): time a ping sweep of one range at several worker counts.

    .\\dev speed 192.168.1.0/24
    .\\dev speed 192.168.1.0/24 --workers 16 32 64 128
    .\\dev speed 192.168.1.0/24 --timeout 0.5

Prints a markdown table to paste into the PR. Only the worker count changes between
runs; the timeout comes from .env (SCAN_PROBE_TIMEOUT_S) or --timeout.
A run takes at most about ceil(hosts / workers) * timeout, so 1 worker on a /24
takes about 4 minutes. Ctrl+C stops it.

This is not a unit test: the times depend on your network and your machine.
"""
import argparse
import math
import time
from dataclasses import dataclass, replace

from backend.services.scan.scan_settings import ScanSettings
from backend.services.scan.sweep_subnet import list_host_addresses, sweep_subnet

DEFAULT_WORKER_COUNTS = [1, 8, 32, 64]


@dataclass(frozen=True)
class SpeedRun:
    workers: int
    rounds: int
    seconds: float
    answered: int


def rounds_for(hosts: int, workers: int) -> int:
    """How many rounds of `workers` pings at once it takes to reach every host."""
    return math.ceil(hosts / workers)


def time_sweep(scope: str, settings: ScanSettings, hosts: int) -> SpeedRun:
    start = time.perf_counter()
    swept, _ = sweep_subnet(scope, settings=settings)
    seconds = time.perf_counter() - start
    return SpeedRun(settings.probe_workers, rounds_for(hosts, settings.probe_workers), seconds, len(swept))


def markdown_table(runs: list[SpeedRun], timeout_s: float) -> str:
    baseline = runs[0].seconds
    lines = [f"| Workers | Rounds | Seconds | Answered | Speed-up vs {runs[0].workers} |",
             "|---:|---:|---:|---:|---:|"]
    for run in runs:
        lines.append(f"| {run.workers} | {run.rounds} | {run.seconds:.1f} | {run.answered} | "
                     f"{baseline / run.seconds:.1f}x |")
    lines.append(f"\nTimeout {timeout_s:g} s per ping.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Time a ping sweep at several worker counts (#81).")
    parser.add_argument("scope", help="the range to sweep, e.g. 192.168.1.0/24")
    parser.add_argument("--workers", type=int, nargs="+", default=DEFAULT_WORKER_COUNTS, metavar="N",
                        help=f"worker counts to try, in order (default: {' '.join(map(str, DEFAULT_WORKER_COUNTS))})")
    parser.add_argument("--timeout", type=float, metavar="SECONDS",
                        help="seconds each ping waits (default: SCAN_PROBE_TIMEOUT_S in .env, or 1)")
    args = parser.parse_args()

    try:
        base = ScanSettings.from_env()
        if args.timeout is not None:
            base = replace(base, probe_timeout_s=args.timeout)
        all_settings = [replace(base, probe_workers=n) for n in args.workers]     # checks every count first
        hosts = len(list_host_addresses(args.scope))
    except ValueError as exc:
        parser.error(str(exc))

    print(f"Sweeping {args.scope} ({hosts} addresses), timeout {base.probe_timeout_s:g} s")
    runs: list[SpeedRun] = []
    for settings in all_settings:
        most = rounds_for(hosts, settings.probe_workers) * settings.probe_timeout_s
        print(f"  {settings.probe_workers:>3} worker(s): up to about {most:.0f} s ...", end="", flush=True)
        run = time_sweep(args.scope, settings, hosts)
        print(f" {run.seconds:.1f} s, {run.answered} answered")
        runs.append(run)

    print("\n" + markdown_table(runs, base.probe_timeout_s))
    print("\nDevices sleep and wake between runs, and the first run warms the ARP cache,"
          " so run it twice if the numbers look odd.")


if __name__ == "__main__":
    main()
