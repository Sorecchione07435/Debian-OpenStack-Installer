# Configure the OpenvSwitch (OVS) Driver for Neutron

import os
import shutil
import json
import time

from pathlib import Path

from ...utils.core.commands import run_command, os_run_output, os_run, run_command_sync
from ...utils.apt.apt import apt_install
from ...utils.config.parser import get
from ...utils.config.setter import set_conf_option
from ...utils.core.system_utils import nc_wait, iface_exists
from ...utils.core import colors
from ...utils.core.system_utils import service_exists, is_debian, is_module_loaded
from ...templates import OVS_BRIDGES_INTERFACES, OVS_DUAL_NIC_BRIDGES_INTERFACES, OVS_PERMISSIONS_SERVICE
from ...utils.network.net_utils import get_network_info
from ...utils.config.helpers import parse_bool
from .utils import enable_ipv4_forwarding

from .network.security_group import add_rules_to_default_sg

from .network.provisioner import create_custom_networks, clean_custom_bridges, add_custom_bridges, bring_up_custom_bridges_ifaces, append_custom_bridges_ifaces_config
from .network.routers import create_custom_network_router

neutron_conf="/etc/neutron/neutron.conf"
conf_ml2="/etc/neutron/plugins/ml2/ml2_conf.ini"
conf_openvswitch="/etc/neutron/plugins/ml2/openvswitch_agent.ini"
conf_dhcp_agent="/etc/neutron/dhcp_agent.ini"
conf_l3_agent="/etc/neutron/l3_agent.ini"
conf_nova="/etc/nova/nova.conf"

def install_pkgs():

    print()

    ovs_packages = [
        "neutron-openvswitch-agent", 
        "neutron-dhcp-agent", 
        "neutron-metadata-agent", 
        "neutron-l3-agent", 
        "openvswitch-switch"]

    if not apt_install(ovs_packages, ux_text=f"Installing Neutron OVS packages...") : return False

    return True

def conf_ovs_bridges(config):

    print()
      
    INTERFACES_FILE = "/etc/network/interfaces.d/openvswitch"

    tenant_network_type = get(config, "neutron.tenant_network.TYPE")
    use_tenant_bridge = tenant_network_type != "vxlan"

    public_iface = get(config, "neutron.ovs.PUBLIC_BRIDGE_INTERFACE")
    public_bridge = get(config, "neutron.ovs.PUBLIC_BRIDGE")
    tenant_bridge = get(config, "neutron.ovs.TENANT_BRIDGE")
    tunnel_bridge = get(config, "neutron.ovs.TUNNEL_BRIDGE")

    ip_address = get(config, "network.HOST_IP")
    ip_address_netmask = get(config, "network.HOST_IP_NETMASK")

    mgmt_gateway = get(config, "network.HOST_MGMT_GATEWAY", None)
    host_default_gateway = get(config, "network.HOST_DEFAULT_GATEWAY", None)

    subnet_gateway = get(config, "neutron.public_network.PUBLIC_SUBNET_GATEWAY")
    subnet_dns = get(config, "neutron.public_network.PUBLIC_SUBNET_DNS_SERVERS")

    management_iface = get(config, "network.HOST_MGMT_INTERFACE")
    bridges = get(config, "neutron.bridges", [])

    start_tag = "IF_INTERNAL_BRIDGE_BEGIN"
    end_tag = "IF_INTERNAL_BRIDGE_END"

    is_dual_nic = (public_iface != management_iface)

    line1 = False
    custom_bridges = bool(bridges)

    is_l3_bridge: bool = mgmt_gateway is not None

    if host_default_gateway:
    
        if iface_exists(public_bridge):
            public_bridge_info = get_network_info(interface_name=public_bridge)
            public_iface_ip = public_bridge_info["ip"]
        else:
            public_iface_info = get_network_info(interface_name=public_iface)
            public_iface_ip = public_iface_info["ip"]

    bridges_to_manage = [public_bridge]

    if tenant_network_type != "vxlan":
        bridges_to_manage.append(tenant_bridge)

    for iface in [public_iface] + bridges_to_manage:
        if iface_exists(iface):
            if iface != management_iface:
                run_command(["ip", "addr", "flush", "dev", iface], f"Flushing IPs on {iface}", ignore_errors=True)
            run_command(["ip", "link", "set", iface, "down"], f"Bringing {iface} down", ignore_errors=True)

    ok, line1 = clean_custom_bridges(bridges=bridges, public_bridge=public_bridge, internal_flat_bridge=tenant_bridge, tunnel_bridge=tunnel_bridge, line1=line1)

    if not ok:
        return False
    
    for bridge, port in [(public_bridge, public_iface)] + ([(tenant_bridge, None)] if tenant_network_type != "vxlan" else []):

        if iface_exists(bridge):
            if port:
                run_command_sync(["ovs-vsctl", "--if-exists", "del-port", bridge, port])
            run_command_sync(["ovs-vsctl", "--if-exists", "del-br", bridge])

    print()

    if isinstance(subnet_dns, list):
        subnet_dns = " ".join(subnet_dns)

    template_file = OVS_DUAL_NIC_BRIDGES_INTERFACES if is_dual_nic else OVS_BRIDGES_INTERFACES

    if os.path.exists(template_file):
        with open(template_file, "r") as f:
            template = f.read()
    else:
        print(f"{colors.RED}Error: template file in '{template_file}' not found{colors.RESET}")
        return False

    if not use_tenant_bridge:
        if start_tag in template and end_tag in template:
            start = template.index(start_tag)
            end = template.index(end_tag) + len(end_tag)
            template = template[:start] + template[end:]

    if host_default_gateway:
    
        host_dns_servers = get(config, "network.HOST_DNS_SERVERS")
        
        public_bridge_ip_config = (
            f"    address {public_iface_ip}\n"
            f"    gateway {host_default_gateway}\n"
            f"    nameservers {" ".join(host_dns_servers)}"
        )

        is_l3_bridge = True
        subnet_address_gateway = ""
    elif mgmt_gateway:
        public_bridge_ip_config = ""

        is_l3_bridge = False

        subnet_address_gateway = f"    gateway {mgmt_gateway}"
    else:
        public_bridge_ip_config = ""
        
        is_l3_bridge = False

        subnet_address_gateway = (
            f"    gateway {subnet_gateway}\n"
            if subnet_gateway
            else ""
        )

    bridges_interfaces_content = template.format(
        management_iface=management_iface if is_dual_nic else "",
        ip_address=ip_address,
        ip_address_netmask=ip_address_netmask,
        subnet_address_gateway=subnet_address_gateway,
        subnet_address_dns_servers=subnet_dns,
        public_iface=public_iface,
        public_bridge=public_bridge,
        internal_bridge=tenant_bridge if use_tenant_bridge else "",
        is_l3="static" if is_l3_bridge else "manual",
        public_bridge_ip_config=public_bridge_ip_config
    )

    if custom_bridges:
        bridges_interfaces_content = append_custom_bridges_ifaces_config(
            bridges,
            bridges_interfaces_content
        )

    with open(INTERFACES_FILE, "w") as f:
        f.write(bridges_interfaces_content)

    interfaces_dir = "/etc/network/interfaces.d/"
    backup_dir = "/root/net-backup"
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = time.strftime("%Y%m%d-%H%M%S")

    for filename in os.listdir(interfaces_dir):
        full_path = os.path.join(interfaces_dir, filename)

        if full_path == INTERFACES_FILE or not os.path.isfile(full_path):
            continue

        backup_name = f"{filename}.{timestamp}"
        backup_path = os.path.join(backup_dir, backup_name)

        shutil.move(full_path, backup_path)

    bridges_to_add = [(public_bridge, public_iface)]

    if tenant_network_type != "vxlan":
        bridges_to_add.append((tenant_bridge, None))
    elif tenant_network_type == "vxlan":
        bridges_to_add.append((tunnel_bridge, None))

    for bridge, port in bridges_to_add:
        if not run_command(["ovs-vsctl", "--may-exist", "add-br", bridge], f"Adding bridge {bridge}"):
            return False
        if port:
            if not run_command(["ovs-vsctl", "--may-exist", "add-port", bridge, port], f"Adding port {port} to {bridge}"):
                return False
            
            print()

            if not run_command(["ip", "link", "set", port, "up"], f"Bringing interface {port} up"):
                return False

        if not run_command(["ip", "link", "set", bridge, "up"], f"Bringing bridge {bridge} up"):
            return False
    
    if custom_bridges:
        print()

        if not add_custom_bridges(bridges=bridges, public_bridge=public_bridge, internal_flat_bridge=tenant_bridge, tunnel_bridge=tunnel_bridge,) : return False

        print()

        if not bring_up_custom_bridges_ifaces(bridges=bridges) : return False

    print()

    networking_cmds = [
        "systemctl disable systemd-networkd",
        "systemctl stop systemd-networkd",
        "systemctl enable networking",
        "systemctl restart networking",
    ]
    full_cmd = " && ".join(networking_cmds)

    if not run_command(["bash", "-c", full_cmd], "Restarting Networking service..."):
        return False

    return True

def conf_neutron_ovs(config):

    ip_address = get(config, "network.HOST_IP")

    tenant_network_type = (get(config, "neutron.tenant_network.TYPE") or "").lower()
    tenant_network_vni_range = (get(config, "neutron.tenant_network.VNI_RANGE") or "")

    provider_networks = get(config, "neutron.provider_networks", [])

    use_tenant_flat_bridge = tenant_network_type != "vxlan"

    flat_networks  = [n["name"] for n in provider_networks if n["type"] == "flat"]
    vlan_networks  = [n["name"] for n in provider_networks if n["type"] == "vlan"]

    bridge_mappings: str = ""

    if use_tenant_flat_bridge:
        bridge_mappings = ",".join(
            f'{n["name"]}:{n["bridge"]}'
            for n in provider_networks
            if n.get("name") and n.get("bridge")
        )
    else:
        for n in provider_networks:
            if n.get("name") and n.get("bridge"):
                bridge_mappings = f'{n["name"]}:{n["bridge"]}'
                break 

    flat_networks_str = ",".join(flat_networks)

    vlan_networks_str = ",".join(vlan_networks)

    create_ovs_bridges = get(config, "neutron.ovs.CREATE_BRIDGES", "no") == "yes" 

    type_drivers = []

    if tenant_network_type:
        type_drivers.append(tenant_network_type)

    for driver in ("flat", "vlan", "vxlan", "local"):
        if driver not in type_drivers:
            type_drivers.append(driver)

    set_conf_option(conf_ml2, "ml2", "type_drivers", ",".join(type_drivers))
    
    if create_ovs_bridges:

        if use_tenant_flat_bridge:
            set_conf_option(conf_ml2, "ml2", "tenant_network_types", "local")
        else:
            set_conf_option(conf_ml2, "ml2", "tenant_network_types", tenant_network_type)

        set_conf_option(conf_ml2, "ml2", "extension_drivers", "port_security")

        if flat_networks_str:
            set_conf_option(conf_ml2, "ml2_type_flat", "flat_networks", flat_networks_str)

        if vlan_networks_str:
            set_conf_option(conf_ml2, "ml2_type_vlan", "network_vlan_ranges", vlan_networks_str)

        set_conf_option(conf_openvswitch, "ovs", "bridge_mappings", bridge_mappings)

        if not use_tenant_flat_bridge:
            
            tunnel_bridge = get(config, "neutron.ovs.TUNNEL_BRIDGE").lower()

            set_conf_option(conf_ml2, "ml2_type_vxlan", "vni_ranges", tenant_network_vni_range)

            set_conf_option(conf_openvswitch, "agent", "l2_population", "true")
            set_conf_option(conf_openvswitch, "agent", "tunnel_types", "vxlan")

            set_conf_option(conf_openvswitch, "ovs", "tunnel_bridge", tunnel_bridge)
            set_conf_option(conf_openvswitch, "ovs", "local_ip", ip_address)
    else:
        set_conf_option(conf_ml2, "ml2", "tenant_network_types", "local")

    set_conf_option(conf_ml2, "securitygroup", "enable_ipset", "true")
    set_conf_option(conf_ml2, "ml2", "mechanism_drivers", "openvswitch")

    set_conf_option(conf_openvswitch, "ovs", "integration_bridge", "br-int")

    set_conf_option(conf_openvswitch, "securitygroup", "enable_security_group", "true")
    set_conf_option(conf_openvswitch, "securitygroup", "firewall_driver", "openvswitch")

    set_conf_option(conf_dhcp_agent, "DEFAULT", "interface_driver", "neutron.agent.linux.interface.OVSInterfaceDriver")
    set_conf_option(conf_dhcp_agent, "DEFAULT", "dhcp_driver", "neutron.agent.linux.dhcp.Dnsmasq")
    set_conf_option(conf_dhcp_agent, "DEFAULT", "enable_isolated_metadata", "true")

    set_conf_option(conf_l3_agent, "DEFAULT", "interface_driver", "neutron.agent.linux.interface.OVSInterfaceDriver")
    set_conf_option(conf_l3_agent, "DEFAULT", "external_network_bridge", "")
    set_conf_option(conf_l3_agent, "DEFAULT", "use_namespaces", "true")
    set_conf_option(conf_l3_agent, "DEFAULT", "debug", "true")

    return True

def finalize(config):

    ip_address = get(config, "network.HOST_IP")

    if not is_module_loaded("openvswitch"):
        print()

        if not run_command(["modprobe", "openvswitch"], f"Loading kernel module 'openvswitch'...") : return False

    modules_file = Path("/etc/modules-load.d/openvswitch.conf")
    
    modules_file.parent.mkdir(parents=True, exist_ok=True)
    modules_file.write_text("openvswitch\n")

    udev_rule = 'SUBSYSTEM=="unix", ACTION=="add", DEVPATH=="/var/run/openvswitch/db.sock", MODE="0666"\n'

    with open("/etc/udev/rules.d/99-openvswitch.rules", "w") as f:
        f.write(udev_rule)

    shutil.copy(OVS_PERMISSIONS_SERVICE, "/etc/systemd/system/ovs-nova-perms.service")

    if not enable_ipv4_forwarding() : return False

    if not run_command(["systemctl", "daemon-reload"], "Reloading system daemon...") : return False
    if not run_command(["systemctl", "enable", "--now", "ovs-nova-perms.service"], "Enabling OVS Nova Permission Service...") : return False

    print()

    if service_exists("nova-api.service"):
        if not run_command(["systemctl", "restart", "nova-api"], "Restarting Nova API...", False, None, 3, 5): return False
  
    if service_exists("neutron-server.service"):
        if not run_command(["systemctl", "restart", "neutron-server", "neutron-openvswitch-agent", "neutron-dhcp-agent", "neutron-metadata-agent", "neutron-l3-agent", "nova-compute"], "Restarting Neutron OVS services...", False, None, 3, 5): return False
    elif service_exists("neutron-api.service") and is_debian():
        if not run_command(["systemctl", "restart", "neutron-api", "neutron-rpc-server", "neutron-l3-agent", "neutron-openvswitch-agent", "neutron-metadata-agent", "nova-compute"], "Restarting Neutron services...", False, None, 3, 5): return False  
    else:
        if not run_command(["systemctl", "restart", "neutron-periodic-workers", "neutron-rpc-server", "apache2", "neutron-openvswitch-agent", "neutron-dhcp-agent", "neutron-metadata-agent", "neutron-l3-agent", "nova-compute"], "Restarting Neutron OVS services...", False, None, 3, 5): return False

    if not nc_wait(ip_address, 9696) : return False

    return True

def create_ovs_networks(config, env):
     
    print()

    public_bridge = get(config, "neutron.ovs.PUBLIC_BRIDGE")
    internal_bridge = get(config, "neutron.ovs.TENANT_BRIDGE")
    tunnel_bridge = get(config, "neutron.ovs.TUNNEL_BRIDGE")

    public_subnet_range_start = get(config, "neutron.public_network.PUBLIC_SUBNET_RANGE_START")
    public_subnet_range_end = get(config, "neutron.public_network.PUBLIC_SUBNET_RANGE_END")

    public_subnet_gateway = get(config, "neutron.public_network.PUBLIC_SUBNET_GATEWAY")
     
    public_subnet_dns_servers = get(config, "neutron.public_network.PUBLIC_SUBNET_DNS_SERVERS")

    public_subnet_cidr = get(config, "neutron.public_network.PUBLIC_SUBNET_CIDR")   

    provider_networks = get(config, "neutron.provider_networks", [])
    
    public_network = next(
        (n for n in provider_networks if n.get("bridge") == public_bridge),
        None
    )

    if public_network is None:
        public_network = next(
            (n for n in provider_networks if n.get("name") == "public"),
            None
        )

    create_ovs_bridges = get(config, "neutron.ovs.CREATE_BRIDGES", "no") == "yes" 

    tenant_network_type = get(config, "neutron.tenant_network.TYPE")

    dns_args = []
    for dns in public_subnet_dns_servers:
        dns_args.extend(["--dns-nameserver", dns])

    networks_list_json = os_run_output(["openstack", "network", "list", "-f", "json"], env=env)
    subnets_list_json = os_run_output(["openstack", "subnet", "list", "-f", "json"], env=env)
    routers_list_json = os_run_output(["openstack", "router", "list", "-f", "json"], env=env)

    networks_list = json.loads(networks_list_json)
    subnets_list = json.loads(subnets_list_json)
    routers_list = json.loads(routers_list_json)

    create_flat_public_network_cmd = [
        "openstack", "network", "create",
                "--share", "--external",
                "--provider-physical-network", public_network["name"],
                "--provider-network-type", "flat",
                public_network["name"]
    ]

    create_flat_internal_network_cmd = ["openstack", "network", "create", "--share",
                "--provider-network-type", "flat", "--provider-physical-network", "internal",
                "internal"]
    
    create_vxlan_internal_network_cmd = [
        "openstack", "network", "create",
        "--share",
        "internal"
    ]

    create_public_network_cmd = []
    create_internal_network_cmd = []

    if create_ovs_bridges:
        create_public_network_cmd = create_flat_public_network_cmd

        if tenant_network_type == "vxlan":
            create_internal_network_cmd = create_vxlan_internal_network_cmd
        else:
            create_internal_network_cmd = create_flat_internal_network_cmd      
    else:
        create_public_network_cmd = ["openstack", "network", "create", "--share", public_network["name"]] 
        create_internal_network_cmd = ["openstack", "network", "create", "internal"]

    public_network_exists = any(net.get("Name") == public_network["name"] for net in networks_list)

    if not public_network_exists:
        if not os_run(
            create_public_network_cmd,
            "Creating public network...", env=env
        ) : return False

        public_subnet_exists = any(sub.get("Name") == "public_subnet" for sub in subnets_list)
        if not public_subnet_exists:
            if not os_run(
                ["openstack", "subnet", "create",
                "--network", public_network["name"],
                "--allocation-pool", f"start={public_subnet_range_start},end={public_subnet_range_end}",
                "--gateway", public_subnet_gateway,
                "--subnet-range", public_subnet_cidr,
                "public_subnet"] + dns_args,
                "Creating public subnet...", env=env
            ) : return False
    else:
        print(f"{colors.YELLOW}Public network already exists, skipping creation.{colors.RESET}")
    
    print()

    internal_network_exists = any(net.get("Name") == "internal" for net in networks_list)

    if not internal_network_exists:
        if not os_run(
            create_internal_network_cmd,
            "Creating internal network...", env=env
            ) : return False

        internal_subnet_exists = any(sub.get("Name") == "internal_subnet" for sub in subnets_list)
        if not internal_subnet_exists:
            if not os_run(
                ["openstack", "subnet", "create", "--network", "internal",
                "--subnet-range", "10.0.0.0/24",
                "--gateway", "10.0.0.1",
                "--allocation-pool", "start=10.0.0.10,end=10.0.0.200",
                "--dns-nameserver", "8.8.8.8",
                "internal_subnet"],
                "Creating internal subnet...", env=env
                ) : return False
    else:
        print(f"{colors.YELLOW}Internal network already exists, skipping creation.{colors.RESET}")
    
    if provider_networks:
        if not create_custom_networks(networks_list=networks_list, subnets_list=subnets_list, provider_networks=provider_networks, public_bridge=public_bridge, tenant_bridge=internal_bridge, tunnel_bridge=tunnel_bridge, env=env) :
            return False

    print()

    router_exists = any(r.get("Name") == "internal_router" for r in routers_list)
    if not router_exists:
        if not os_run(
            ["openstack", "router", "create", "internal_router"],
            "Creating internal router...", env=env
        ): return False
    else:
        print(f"{colors.YELLOW}Internal Router already exists, skipping creation.{colors.RESET}")

    if create_ovs_bridges:
        external_gateways_list = json.loads(os_run_output(["openstack", "router", "show", "internal_router", "-f", "json", "-c", "external_gateway_info"], env=env))
        interfaces_info_list = json.loads(os_run_output(["openstack", "router", "show", "internal_router", "-f", "json", "-c", "interfaces_info"], env=env))

        if not external_gateways_list.get("external_gateway_info"):
            if not os_run(
                ["openstack", "router", "set", "internal_router", "--external-gateway", public_network["name"]],
                "Setting external gateway for internal router...", env=env
            ):
                return False

        if not interfaces_info_list.get("interfaces_info"):
            if not os_run(
                ["openstack", "router", "add", "subnet", "internal_router", "internal_subnet"],
                "Adding internal subnet to router...", env=env
            ):
                return False    
            
        if provider_networks:
            if not create_custom_network_router(routers_list=routers_list, provider_networks=provider_networks, tenant_bridge=internal_bridge, public_bridge=public_bridge, tunnel_bridge=tunnel_bridge, env=env) : return False
    
    sg_list_json = os_run_output(["openstack", "security", "group", "list", "-f", "json"], env=env)
    sg_list = json.loads(sg_list_json)

    matching_sgs = [sg for sg in sg_list if sg["Name"] == "default"]
    if not matching_sgs:
        raise RuntimeError("No security group named 'default' found")
    sg_id = matching_sgs[0]["ID"]

    rules_json = os_run_output(["openstack", "security", "group", "rule", "list", sg_id, "-f", "json"], env=env)
    rules = json.loads(rules_json)

    services_rules = get(config, "neutron.default_security_group.services", {})
    services_rules_remote_ip_prefix = get(config, "neutron.default_security_group.defaults.remote_ip_prefix")

    if services_rules:
        print()
        if not add_rules_to_default_sg(create_bridges=create_ovs_bridges, rules_dict=services_rules, ip_prefix=services_rules_remote_ip_prefix, sg_id=sg_id, rules=rules, env=env) : return False

    return True

def run_setup_ovs_neutron(config, env):

    create_ovs_bridges = parse_bool(get(config, "neutron.ovs.CREATE_BRIDGES"), False)

    if not install_pkgs(): return False
    
    if create_ovs_bridges:
        if not conf_ovs_bridges(config) : return False
    
    if not conf_neutron_ovs(config) : return False
    if not finalize(config) : return False   
    if not create_ovs_networks(config, env): return False

    return True
