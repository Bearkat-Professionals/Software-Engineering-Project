import ipaddress
from backend.black_boxes.read_arp_cache import read_arp_cache
from backend.services.scan.discovered_host import DiscoveredHost
from backend.shared_modules.validate_input import validate_ip, validate_scope

class KnownHostsResult:
    def __init__(self, hosts, rejected, arp_status):      
        self.hosts = hosts    #stores the hosts that are in the scope and have valid IPs
        self.rejected = rejected        #stores the addresses that are invalid or out of scope
        self.arp_status = arp_status     #could be read as either ok or unavailable, depending on fully if the apr cache was read or not.
def read_known_hosts(scope, user_addresses=None, read_arp=read_arp_cache):
    
    result = validate_scope(scope)    #runs sh-1 check to see if the scope is valid and if it is, it returns the scope in a usable format. if not, retuns why its invalid
    if result.valid == False:         
        raise ValueError(result.reason)
    
    network = ipaddress.IPv4Network(result.value)    #tursn the range into a format python could read
    all_hosts = []
    rejected = []
    arp_status = "ok"
    
    try:
        entries = read_arp() #reads the arp cache, the list of devices that have been seen
    except Exception:       
        entries = None        # if the arp cache is not available, it sets the entries to None
    if entries == None:
        entries = []           # if the arp cache is not available, sets to an empty list returns unavailable
        arp_status = "unavailable"
    
    for entry in entries:  #Looks at devices in the arp cache. entry coems with ip address and mac
        check = validate_ip(entry.ip_address)  #checks if its real, broke ones are skipped
        if check.valid == True:
            if ipaddress.IPv4Address(check.value) in network: #checks if the address is in the range of the scan
                new_host = DiscoveredHost(check.value, entry.mac_address) #Fillss host record with ip and mac
                all_hosts.append(new_host)

    
    if type(user_addresses) == str:
        user_addresses = [user_addresses]
    if user_addresses != None:
        for addr in user_addresses:
            check = validate_ip(addr)
            
            
            if check.valid == False:
                error_tuple = (addr, check.reason)
                rejected.append(error_tuple)
            else:
                if ipaddress.IPv4Address(check.value) not in network:
                    fail_msg = str(check.value) + " is outside the scan range " + str(network) + "."
                    rejected.append((addr, fail_msg))
                else:
                    
                    user_host = DiscoveredHost(check.value)
                    all_hosts.append(user_host)
    
    final_output = KnownHostsResult(all_hosts, rejected, arp_status)
    return final_output
