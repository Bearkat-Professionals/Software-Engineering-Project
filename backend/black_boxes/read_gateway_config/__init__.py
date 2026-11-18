"""BB-2 Read Gateway Config (black box): this computer's default gateway, without admin rights.

    from backend.black_boxes.read_gateway_config import read_gateway, Gateway

    read_gateway()                  -> Gateway, or None when there's none or it can't be read
    read_gateway("192.168.1.212")   -> the gateway of the adapter with that address

Used by 2.2.5.1 Build Logical Topology (WF-06: "identifies the gateway, when possible").
"""
from backend.black_boxes.read_gateway_config.gateway import DefaultRoute, Gateway
from backend.black_boxes.read_gateway_config.read_gateway_config import read_default_routes, read_gateway

__all__ = ["DefaultRoute", "Gateway", "read_default_routes", "read_gateway"]
