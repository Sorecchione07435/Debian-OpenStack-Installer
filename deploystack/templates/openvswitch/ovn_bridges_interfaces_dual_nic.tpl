auto lo
iface lo inet loopback

auto {management_iface}
iface {management_iface} inet static
    address {ip_address}
    netmask {ip_address_netmask}
{subnet_address_gateway}    
    dns-nameservers {subnet_address_dns_servers}

auto {public_iface}
iface {public_iface} inet manual
    pre-up ovs-vsctl --may-exist add-br {public_bridge}
    pre-up ovs-vsctl --may-exist add-port {public_bridge} {public_iface}
    up ip link set {public_iface} up
    down ip link set {public_iface} down

auto {public_bridge}
iface {public_bridge} inet {is_l3}
{public_bridge_ip_config}
    pre-up ovs-vsctl --may-exist add-br {public_bridge}
    pre-up ovs-vsctl --may-exist add-port {public_bridge} {public_iface}
    pre-up ip link set {public_iface} up