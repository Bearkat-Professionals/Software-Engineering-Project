r"""Manual check for BB-2 against this machine's real routes (the ticket's "Done when").

    .\dev gw                          every default route, and the one a plain lookup picks
    .\dev gw --for 192.168.1.0/24     the one facing that network (as a scan would choose)

    python3 dev.py gw ...             on Linux and macOS

This is not a unit test: what it prints depends on the machine and network you are on.
"""
import argparse

from backend.black_boxes.read_gateway_config import read_default_routes
from backend.black_boxes.read_gateway_config.parser import explain_choice
from backend.services.scan.collect_ssdp_evidence import local_address_for
from backend.services.scan.sweep_subnet import list_host_addresses

def main() -> None:
    parser = argparse.ArgumentParser(description="Print this machine's default routes and gateway (BB-2).")
    parser.add_argument("--for", dest="scope", metavar="RANGE",
                        help="choose the gateway facing this network, e.g. 192.168.1.0/24")
    args = parser.parse_args()

    local_ip = None
    if args.scope:
        try:
            local_ip = local_address_for(list_host_addresses(args.scope))
        except ValueError as exc:
            parser.error(str(exc))
        print(f"This computer's address on {args.scope}: {local_ip or '(unknown)'}")

    routes = read_default_routes(local_ip)
    if routes is None:
        print("Routing table unavailable (it could not be read on this machine).")
        return
    print(f"{len(routes)} default route(s):")
    for route in routes:
        metric = "" if route.metric is None else f"  metric {route.metric}"
        print(f"  via {route.gateway:<16} on {route.interface}  (local {route.local_ip or '?'}){metric}")
    gateway, why = explain_choice(routes, local_ip)
    print(f"\nGateway: {gateway.ip} on {gateway.interface}, because it's {why}." if gateway
          else f"\nNo gateway: {why}.")

if __name__ == "__main__":
    main()
    