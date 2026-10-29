import ipaddress
from backend.shared_modules.validate_input import validate_scope

def filter_addresses(hosts,scope):
    result = validate_scope(scope) #checks that its a real, private range of a normal size
    if not result.valid:          
        raise ValueError(result.reason)          #Used when the scope is bad, so nothing scans 
    network = ipaddress.IPv4Network(result.value)
    horrible_addresses = ["0.0.0.0","255.255.255.255"]
    if network.prefixlen < 31:                 #prefixlen = network_size
        horrible_addresses.append(str(network.network_address))       #tunrs it into string so python could read it
        horrible_addresses.append(str(network.broadcast_address))     #same process
    multicast = ipaddress.IPv4Network("224.0.0.0/4")
    loopback = ipaddress.IPv4Network("127.0.0.0/8")

    kept = []

    for host in hosts:
        try:
            address = ipaddress.IPv4Address(host.ip_address)
        except ValueError:
            continue
        if str(address) in horrible_addresses:     #Checks if the address in one of the fake addresses assign above
            continue
        if address in multicast:      #checks if the address is in the multicast range
            continue
        if address in loopback:       #same as above but for Loopback range
            continue

        found = False
        for i in range(len(kept)):    #Checks if the host is already in the list of kept hosts.
            if kept[i].ip_address == host.ip_address:
                found = True          #  If it is, it checks if the new host has a MAC address.
                if host.mac_address and not kept[i].mac_address:   # if the new host has a MAC address and the old one doesnt, it replaces the old one with the new one
                    kept[i] = host
                break
        if not found:          #IP already in the list of kep hosts
            kept.append(host)  # new copy has a MAC address, old one doessnt
    return kept                # swap in the copy with the MAC address.
                        
            

                    


