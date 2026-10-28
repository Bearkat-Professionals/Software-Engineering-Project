import ipaddress
from backend.shared_modules.validate_input import validate_scope
from backend.black_boxes.timed_network_request import ProbeStatus, ping, run_probes

#Host Address
def list_host_addresses(scope):
    result = validate_scope(scope)
    if not result:
        return None
    network = ipaddress.ip_network(result.value)
    return [str(address) for address in network.hosts()]

#Sweep Subnet
def sweep_subnet(scope, probe = ping, max_workers = 32):
    addresses = list_host_addresses(scope)
    if addresses is None:
        return[], "unavailable"
    
    results = run_probes(addresses, probe, max_workers)

    if all(result.status == ProbeStatus.UNAVAILABLE for result in results):
        return[], "unavailable"
    
    responders = [result.target for result in results if result.responded]
    return responders, "ok"