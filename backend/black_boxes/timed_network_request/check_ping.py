"""Manual check for BB-3 against the real network (the ticket's "Done when").

    python -m backend.black_boxes.timed_network_request.check_ping 192.168.1.1
    python -m backend.black_boxes.timed_network_request.check_ping 192.168.1.1 192.168.1.250 --sweep 192.168.1.0/24

    or

    py -m backend.black_boxes.timed_network_request.check_ping 192.168.1.1
    py -m backend.black_boxes.timed_network_request.check_ping 192.168.1.1 192.168.1.250 --sweep 192.168.1.0/24

This is not a unit test: what it prints depends on the network you are on.
"""
import argparse
import ipaddress
import time
from collections import Counter

from backend.black_boxes.timed_network_request import ProbeResult, ping, run_probes

# Same limit SH-1 will enforce on scan scopes, so a typo like /8 can't start a huge sweep
MAX_SWEEP_ADDRESSES = 1024


def show(result: ProbeResult) -> None:
    print(f"  {result.target:<16} {result.status.value:<12} {result.elapsed_ms:>5} ms  {result.detail or ''}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ping addresses through BB-3, and optionally sweep a range.")
    parser.add_argument("addresses", nargs="*", help="addresses to ping one at a time, e.g. your gateway")
    parser.add_argument("--sweep", metavar="CIDR", help="also ping a whole range in parallel, e.g. 192.168.1.0/24")
    args = parser.parse_args()
    if not args.addresses and not args.sweep:
        parser.error("give at least one address or --sweep")

    if args.addresses:
        print("Single pings:")
        for address in args.addresses:
            show(ping(address))

    if args.sweep:
        try:
            network = ipaddress.ip_network(args.sweep, strict=False)
        except ValueError as exc:
            parser.error(str(exc))
        if network.num_addresses > MAX_SWEEP_ADDRESSES:
            parser.error(f"{args.sweep} has {network.num_addresses} addresses; the limit is {MAX_SWEEP_ADDRESSES}")

        hosts = [str(host) for host in network.hosts()]
        print(f"\nSweeping {len(hosts)} addresses in {network} ...")
        start = time.perf_counter()
        results = run_probes(hosts, ping)
        seconds = time.perf_counter() - start

        counts = Counter(result.status.value for result in results)
        print(f"Finished in {seconds:.1f} s: " + ", ".join(f"{n} {status}" for status, n in counts.items()))
        for result in results:
            if result.responded or result.detail:      # skip the plain no_reply addresses
                show(result)


if __name__ == "__main__":
    main()