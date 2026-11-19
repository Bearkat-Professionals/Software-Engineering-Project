"""BB-2's records: a default route as the system lists it, and the gateway chosen."""
from dataclasses import dataclass


@dataclass(frozen=True)
class DefaultRoute:
    """One default route (0.0.0.0/0) from the routing table."""
    gateway: str                    
    interface: str                  
    local_ip: str | None = None     
    metric: int | None = None       


@dataclass(frozen=True)
class Gateway:
    ip: str                         
    interface: str      
                