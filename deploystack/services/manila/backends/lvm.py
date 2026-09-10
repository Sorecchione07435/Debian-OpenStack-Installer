# Configure the LVM Backend (Share Node)

import os
import pwd
import grp
import subprocess
import tempfile
import ipaddress
import json

from pathlib import Path

from ....utils.core.commands import run_command, os_run, os_run_output, run_commands, run_command_sync, run_command_output
from ....utils.apt.apt import apt_install

from ....utils.config.parser import get, get_conf_option
from ....utils.config.setter import set_conf_option

from ....utils.config.helpers import parse_bool

from ....utils.lvm.loopback import write_loopback_lvm_env, setup_loopback_service
from ....utils.lvm import get_vg_for_pv, ensure_system_user_with_run_command

from .utils import wait_manila_backend, create_manila_sudoers_rule

from .utils.shares import create_shares, create_share_types

from ....utils.core.system_utils import service_exists, is_debian, is_ubuntu_release, is_package_installed

from ...patches.manila.directio import run_setup_directio_patch

from .protocols.nfs import run_setup_nfs
from .protocols.samba import run_setup_samba

from ....utils.core import colors

manila_conf = "/etc/manila/manila.conf"

conf_ml2 = "/etc/neutron/plugins/ml2/ml2_conf.ini"
conf_openvswitch = "/etc/neutron/plugins/ml2/openvswitch_agent.ini"

def install_pkgs():

    print()

    if not apt_install(["manila-share", "lvm2", "nfs-kernel-server"], "Installing Manila Share LVM Packages..."):
        return False
    
    return True

def conf_shares_bridge(config):

    INTERFACES_FILE = "/etc/network/interfaces.d/br-shares"

    share_export_ip = get(config, "manila.backends.lvm.SHARE_EXPORT_IP")
    neutron_driver = config.get("neutron", {}).get("DRIVER", "ovs").lower()

    share_export_ip = ipaddress.ip_interface(f"{share_export_ip}/24")
    share_export_gateway_ip = str(share_export_ip.network.network_address + 1)

    print()

    if not run_command(["ovs-vsctl", "--may-exist", "add-br", "br-shares"], "Adding shares bridge...") : return False

    shares_config = f"""
auto br-shares
iface br-shares inet static
    address {share_export_ip.ip}
    netmask 255.255.255.0
    post-up ip route add 10.0.0.0/24 via {share_export_gateway_ip} dev br-shares
    pre-down ip route del 10.0.0.0/24 via {share_export_gateway_ip} dev br-shares
"""

    with open(INTERFACES_FILE, "w") as f:
        f.write(shares_config)

    print()

    with open("/etc/sysctl.d/99-br-shares.conf", "w") as f:
        f.write("net.ipv4.conf.br-shares.rp_filter = 2\n")
    run_command(["sysctl", "--system"], "Applying sysctl...")

    print()

    run_command(["ip", "addr", "flush", "dev", "br-shares"], f"Flushing IPs on shares bridge", ignore_errors=True)
    run_command(["ip", "link", "set", "br-shares", "down"], f"Bringing shares bridge down", ignore_errors=True)

    print()

    if not run_command(["systemctl", "restart", "networking"], "Restarting Networking service..."): return False

    flat_networks_mappings = get_conf_option(conf_ml2, "ml2_type_flat", "flat_networks")

    networks = [n for n in flat_networks_mappings.split(",") if n]

    if "shares" not in networks:
        networks.append("shares")

    set_conf_option(conf_ml2, "ml2_type_flat", "flat_networks", ",".join(networks))

    if neutron_driver == "ovn":

        print()

        ovn_bridge_mappings = get_conf_option(conf_ml2, "ovn", "ovn_bridge_mappings")

        bridge_mappings = [n for n in ovn_bridge_mappings.split(",") if n]

        if "shares:br-shares" not in bridge_mappings:
            bridge_mappings.append("shares:br-shares")

        ovn_bridge_mappings_str = ",".join(bridge_mappings)

        set_conf_option(conf_ml2, "ovn", "ovn_bridge_mappings", ovn_bridge_mappings_str)

        if not run_command(["ovs-vsctl", "set", "open", ".", f"external-ids:ovn-bridge-mappings={ovn_bridge_mappings_str}"], "Updating OVN bridge mappings...") : return False

        print()

        ovs_services = ["systemctl", "restart",
            "ovn-ovsdb-server-nb",
            "ovn-ovsdb-server-sb",
            "ovn-northd",
            "ovn-controller",
            "nova-compute"]
        
        if service_exists("neutron-api.service") and not service_exists("neutron-server.service"):
            ovs_services.append("neutron-api")
        elif service_exists("neutron-periodic-workers.service") and not service_exists("neutron-server.service"):
            ovs_services.append("neutron-periodic-workers.service")
            ovs_services.append("apache2.service")
        else:
            ovs_services.append("neutron-server")
            
        if not run_command(ovs_services, "Restarting OVN services...", False, None, 3, 5):  return False

    elif neutron_driver == "ovs":

        ovs_bridge_mappings = get_conf_option(conf_openvswitch, "ovs", "bridge_mappings")

        bridge_mappings = [n for n in ovs_bridge_mappings.split(",") if n]

        if "shares:br-shares" not in bridge_mappings:
            bridge_mappings.append("shares:br-shares")

        ovs_bridge_mappings_str = ",".join(bridge_mappings)

        set_conf_option(conf_openvswitch, "agent", "tunnel_types", "vxlan")
        set_conf_option(conf_openvswitch, "ovs", "bridge_mappings", ovs_bridge_mappings_str)

        print()  

        if service_exists("neutron-server.service"):
            if not run_command(["systemctl", "restart", "neutron-server", "neutron-openvswitch-agent", "neutron-dhcp-agent", "neutron-metadata-agent", "neutron-l3-agent", "nova-compute"], "Restarting Neutron OVS services...", False, None, 3, 5): return False
        elif service_exists("neutron-api.service") and is_debian():
            if not run_command(["systemctl", "restart", "neutron-api", "neutron-rpc-server", "neutron-l3-agent", "neutron-openvswitch-agent", "neutron-metadata-agent", "nova-compute"], "Restarting Neutron services...", False, None, 3, 5): return False  
        else:
            if not run_command(["systemctl", "restart", "neutron-periodic-workers", "apache2", "neutron-openvswitch-agent", "neutron-dhcp-agent", "neutron-metadata-agent", "neutron-l3-agent", "nova-compute"], "Restarting Neutron OVS services...", False, None, 3, 5): return False
    
    return True


def setup_iptables_rules(config):

    protocols = get(config, "manila.SHARE_PROTOCOLS", default=["NFS"])
    enabled_share_protocols = ",".join(protocols)

    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(
            "iptables-persistent iptables-persistent/autosave_v4 boolean true\n"
            "iptables-persistent iptables-persistent/autosave_v6 boolean false\n"
        )
        seed_file = f.name

    try:
        if not run_command_sync(["debconf-set-selections", seed_file]):
            return False
    finally:
        os.remove(seed_file)
    
    if not is_package_installed("iptables-persistent"):
        print()
        
        if not apt_install(["iptables-persistent"], "Installing IP Tables Persistent package...") : return False

    print()

    def _iptables_rule_exists(rule_args):
        result = subprocess.run(
            ["iptables", "-C"] + rule_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return result.returncode == 0

    def _iptables_chain_exists(chain_name):
        result = subprocess.run(
            ["iptables", "-nL", chain_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return result.returncode == 0

    def ensure_chain(chain_name):
        if not _iptables_chain_exists(chain_name):
            if not run_command(["iptables", "-N", chain_name], f"Creating chain {chain_name}..."):
                return False
        return True

    if not run_command(["iptables", "-P", "OUTPUT", "ACCEPT"], "Setting OUTPUT policy..."):
        return False

    def ensure_rule(chain, *rule_args, description=None):
        full_rule = [chain] + list(rule_args)
        if not _iptables_rule_exists(full_rule):
            cmd = ["iptables", "-A"] + full_rule
            if not run_command(cmd, description):
                return False
        return True

    if not ensure_rule(
        "INPUT", "-i", "br-shares",
        "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED",
        "-j", "ACCEPT", description="Allowing established/related traffic on br-shares..."):
        return False

    if not ensure_chain("BR_SHARES"):
        return False

    print()

    if not run_command(["iptables", "-F", "BR_SHARES"], "Flushing br_shares chain..."):
        return False

    if not ensure_rule("INPUT", "-i", "br-shares", "-j", "BR_SHARES", description="Routing br-shares traffic to BR_SHARES chain..."):
        return False

    print()

    br_shares_rules = []

    if "NFS" in enabled_share_protocols:
        br_shares_rules.append(["-p", "tcp", "--dport", "2049", "-j", "ACCEPT"])
        br_shares_rules.append(["-p", "udp", "--dport", "2049", "-j", "ACCEPT"])

    if "CIFS" in enabled_share_protocols:
        br_shares_rules.append(["-p", "tcp", "--dport", "445", "-j", "ACCEPT"])
        br_shares_rules.append(["-p", "udp", "--dport", "445", "-j", "ACCEPT"])

    br_shares_rules.append(["-j", "DROP"])

    iptables_commands = [["iptables", "-A", "BR_SHARES"] + r for r in br_shares_rules]

    if not run_commands(iptables_commands, "Applying firewall rules..."):
        return False

    print()

    if not run_command(["netfilter-persistent", "save"], "Saving iptables rules...") : return False

    return True

def create_shares_network(config, env):

    share_export_ip = get(config, "manila.backends.lvm.SHARE_EXPORT_IP")

    neutron_driver = config.get("neutron", {}).get("DRIVER", "ovs").lower()
    create_bridges = (
            get(config, "neutron.ovn.CREATE_BRIDGES", "no").lower() == "yes"
            if neutron_driver == "ovn"
            else get(config, "neutron.ovs.CREATE_BRIDGES", "no").lower() == "yes"
        )

    share_ip = ipaddress.ip_interface(f"{share_export_ip}/24")

    share_ip_cidr = share_ip.network
    share_gateway_ip = str(share_ip.network.network_address + 1)

    networks_list = json.loads(os_run_output(["openstack", "network", "list", "-f", "json"], env=env))

    shares_network_exists = any((net.get("name") or net.get("Name")) == "shares" for net in networks_list)

    shares_subnet_id = ""

    if not shares_network_exists:
        print()
        if not os_run(["openstack", "network", "create", "shares", "--provider-network-type", "flat", "--provider-physical-network", "shares", "--share"], "Creating shares network...", env=env): return False

    subnets_list = json.loads(os_run_output(["openstack", "subnet", "list", "-f", "json"], env=env))
    shares_subnet_exists = any((sub.get("Name") or sub.get("name")) == "shares_subnet" for sub in subnets_list)

    if not shares_subnet_exists:
        if not os_run(["openstack", "subnet", "create", "shares_subnet", "--subnet-range", str(share_ip_cidr), "--gateway", share_gateway_ip, "--no-dhcp", "--network", "shares"], "Creating shares subnet...", env=env) : return False

    if create_bridges:
        shares_subnet_id = os_run_output(["openstack", "subnet", "show", "shares_subnet", "-f", "value", "-c", "id"], env=env).strip()
        internal_router_info = json.loads(os_run_output(["openstack", "router", "show", "internal_router", "-f", "json"], env=env))

        interfaces = internal_router_info.get("interfaces_info", [])
        shares_router_subnet_attached = any(iface.get("subnet_id") == shares_subnet_id for iface in interfaces)

        if not shares_router_subnet_attached:
            print()

            if not os_run(["openstack", "router", "add", "subnet", "internal_router", shares_subnet_id], "Adding shares subnet to internal router...", env=env): return False

    return True

def conf_lvm(config):

    lvm_physical_volume = get(config, "manila.backends.lvm.storage.PHYSICAL_VOLUME")
    lvm_image_file_path = get(config, "manila.backends.lvm.storage.MANILA_LVM_IMAGE_FILE_PATH")
    lvm_loop_dev = get(config, "manila.backends.lvm.storage.MANILA_LVM_LOOP_PATH")
    lvm_image_size_in_gb = get(config, "manila.backends.lvm.storage.MANILA_LVM_IMAGE_SIZE_IN_GB")

    vg_name = get(config, "manila.backends.lvm.storage.SHARE_VOLUME_GROUP")

    if lvm_physical_volume:
        lvm_dev = lvm_physical_volume
    else:
        lvm_dev = lvm_loop_dev

        image_path = Path(lvm_image_file_path)
        image_path.parent.mkdir(parents=True, exist_ok=True)

        if not os.path.exists(lvm_image_file_path):

            print() 

            truncate_cmd = [
                "truncate",
                "-s",
                f"{lvm_image_size_in_gb}G",
                lvm_image_file_path
            ]

            if not run_command(truncate_cmd, "Allocating LVM disk image..."):
                return False

            if not ensure_system_user_with_run_command("manila"):
                return False

            uid = pwd.getpwnam("manila").pw_uid
            gid = grp.getgrnam("manila").gr_gid

            os.chown(lvm_image_file_path, uid, gid)
            os.chmod(lvm_image_file_path, 0o600)

            print()

        try:
            losetup_output = subprocess.check_output(
                ["losetup", "-j", lvm_image_file_path],
                text=True
            )
        except subprocess.CalledProcessError:
            losetup_output = ""

        if lvm_image_file_path not in losetup_output:
            if not run_command(
                ["losetup", lvm_loop_dev, lvm_image_file_path],
                f"Associating {lvm_image_file_path} to {lvm_loop_dev}..."
            ):
                return False
            
    vg = get_vg_for_pv(lvm_dev)

    if vg is None:

        print() 

        if not run_command(
            ["pvcreate", lvm_dev],
            f"Creating physical volume on {lvm_dev}..."
        ):
            return False

        if not run_command(
            ["vgcreate", vg_name, lvm_dev],
            f"Creating volume group {vg_name}..."
        ):
            return False

    elif vg == vg_name:
        pass

    else:
        print(
            f"{colors.RED}"
            f"{lvm_dev} already belongs to VG '{vg}', expected '{vg_name}'"
            f"{colors.RESET}"
        )
        return False

    return True

def conf_lvm_manila(config):

    backend_name = get(config, "manila.backends.lvm.BACKEND_NAME").lower()

    protocols = get(config, "manila.SHARE_PROTOCOLS", default=["NFS"])
    enabled_share_protocols = ",".join(protocols)

    vg_name = get(config, "manila.backends.lvm.storage.SHARE_VOLUME_GROUP")
    

    share_export_ip = get(config, "manila.backends.lvm.SHARE_EXPORT_IP")

    share_helpers = get(config, "manila.SHARE_HELPERS") or []

    helpers = []

    if "NFS" in protocols:
        if not run_setup_nfs(): return False

    if "CIFS" in protocols:
        if not run_setup_samba(config): return False

    for helper in share_helpers:
        for helper_type, config in helper.items():
            helper_name = config.get("name")
            helpers.append(f"{helper_type}={helper_name}")

    helpers = [f"{helper_type}={config.get('name')}" for helper in share_helpers for helper_type, config in helper.items()]

    set_conf_option(manila_conf, "DEFAULT", "share_helpers", ",".join(helpers))

    set_conf_option(manila_conf, "DEFAULT", "enabled_share_backends", "lvm")
    set_conf_option(manila_conf, "DEFAULT", "enabled_share_protocols", enabled_share_protocols)

    set_conf_option(manila_conf, "lvm", "share_backend_name", backend_name)
    set_conf_option(manila_conf, "lvm", "share_driver", "manila.share.drivers.lvm.LVMShareDriver")
    set_conf_option(manila_conf, "lvm", "lvm_share_volume_group", vg_name)
    set_conf_option(manila_conf, "lvm", "lvm_share_export_ips", share_export_ip)
    set_conf_option(manila_conf, "lvm", "driver_handles_share_servers", "False")

    return True
    
def finalize(env):

    print()

    if not run_command(["systemctl", "daemon-reload"], "Reloading systemd daemon..."): return False

    print()

    create_manila_sudoers_rule()

    if not run_command(["systemctl", "restart", "manila-share"], "Restarting Manila Share services...", False, None, 3, 5):
        return False

    print()
    
    if not wait_manila_backend(env=env) : return False

    return True

def finalize_lvm_backend(config, env):

    create_shares_enabled = parse_bool((get(config, "manila.CREATE_SHARES") or "").lower(), False)

    default_type_shares = get(config, "manila.share_types") or []

    if not create_share_types(default_type_shares=default_type_shares, env=env): return False

    if create_shares_enabled:
        shares = get(config, "manila.shares") or []

        if not create_shares(shares=shares, env=env, dhss=False): return False

    return True

def run_setup_lvm_backend(config, env):

    if not install_pkgs(): return False

    if not conf_lvm(config): return False

    using_loopback = not get(config, "manila.backends.lvm.storage.PHYSICAL_VOLUME")

    if using_loopback:
        if not write_loopback_lvm_env("manila", description="Manila Loopback LVM", before_services="manila-share.service"): return False   
        if not setup_loopback_service("manila"): return False   

        if not is_debian() and is_ubuntu_release("26.04"):
            if not run_setup_directio_patch(): return False

    if not conf_lvm_manila(config): return False

    if not conf_shares_bridge(config): return False
    if not create_shares_network(config, env) : return False
    if not setup_iptables_rules(config): return False

    if not finalize(env): return False
    if not finalize_lvm_backend(config, env=env): return False

    return True