import os
import shutil
import uuid
import yaml
import ipaddress

import sys

from ...utils.network.net_utils import get_network_info
from ...utils.core.system_utils import has_hw_virtualization, get_free_loops, generate_password

from ...templates import OPENSTACK_CONFIG_TEMPLATE

from ...utils.config.helpers import parse_bool

from ...utils.core import colors

config_file_path = ""

def _remove_empty(d):
    if isinstance(d, dict):
        return {k: _remove_empty(v) for k, v in d.items() if v != "" and v is not None}
    if isinstance(d, list):
        return [_remove_empty(i) for i in d if i != "" and i is not None]
    return d

def generate_config_file() -> str:

    global config_file_path
    config_file_path = f"/root/openstack-config-{uuid.uuid4().hex}.yaml"
    script_dir = os.path.dirname(os.path.realpath(__file__))
    src_file = os.path.join(script_dir, OPENSTACK_CONFIG_TEMPLATE)
    shutil.copy(src_file, config_file_path)

    return config_file_path

def config_openstack(
    install_horizon: str = "yes",
    install_cinder: str = "yes",
    install_manila: str = "no",
    config_file_path: str = "",

    cinder_enabled_backends: str = "",
    cinder_nfs_snapshots_enabled: str = "",

    cinder_lvm_vg = "",
    cinder_physical_volume = "",

    manila_lvm_vg = "",
    manila_lvm_physical_volume = "",
    cinder_lvm_image_size_in_gb=None,
    manila_lvm_image_size_in_gb=None,
    neutron_driver: str = "ovs",   # "ovs" | "ovn"
    manila_backend: str = "",
    manila_share_protocols: str = "",
    os_release: str = "caracal",
    os_mgmt_iface: str = "",

    os_mgmt_gateway: str = "",
    default_gateway: str = "",

    enable_cinder_backup = "no",
    cinder_backup_driver = "",
    compression_algorithm = "",
    backup_file_size_in_bytes = 0,
    backup_sha_block_size_in_bytes = 0,
    backup_workers = 0
):

    try:
        with open(config_file_path, "r") as f:
            config_dict = yaml.safe_load(f) or {}
    except FileNotFoundError:
        config_dict = {}

    info = get_network_info()
    iface = info["interface"]

    ip = info["ip"]
    netmask = info["netmask"]

    if default_gateway is None:
        gateway = info["gateway"]
    else:
        gateway = default_gateway

    ip_cidr = info["network_cidr"]
    network = info["network"]

    mgmt_iface = None

    mgmt_ip = None
    mgmt_ip_cidr = None
    mgmt_netmask = None

    last_ip = str(ipaddress.IPv4Address(ipaddress.IPv4Network(ip_cidr, strict=False).broadcast_address - 1))

    dns_list = []

    virt_type = "kvm" if has_hw_virtualization() else "qemu"

    start_ip = str(ipaddress.IPv4Address(int(ipaddress.IPv4Address(ip)) + 50))

    # Password
    config_dict.setdefault("passwords", {})
    for key in ["ADMIN_PASSWORD", "SERVICE_PASSWORD", "RABBITMQ_PASSWORD", "DATABASE_PASSWORD", "DEMO_PASSWORD"]:
        config_dict["passwords"][key] = generate_password()

    # Rete
    config_dict.setdefault("network", {})

    if os_mgmt_iface:
        try:
            mgmt_iface_info = get_network_info(interface_name=os_mgmt_iface)
        except ValueError:
            os.remove(config_file_path)

            print(f"{colors.RED}The '{os_mgmt_iface}' management interface does not have an assigned IPv4 address. Configure it (DHCP or static IP) before starting the deployment.{colors.RESET}")
            sys.exit(1)

        mgmt_iface = os_mgmt_iface
        mgmt_ip = mgmt_iface_info["ip"]
        mgmt_ip_cidr = mgmt_iface_info["network_cidr"]
        mgmt_netmask = mgmt_iface_info["netmask"]
    else:
        mgmt_iface = iface

        mgmt_ip = ip
        mgmt_ip_cidr = ip_cidr
        mgmt_netmask = netmask

    config_dict["network"]["HOST_IP"] = mgmt_ip
    config_dict["network"]["HOST_IP_NETMASK"] = mgmt_netmask
    config_dict["network"]["HOST_IP_CIDR"] = mgmt_ip_cidr

    if os_mgmt_gateway:
        config_dict["network"]["HOST_MGMT_GATEWAY"] = os_mgmt_gateway
    else:
        config_dict["network"]["HOST_DEFAULT_GATEWAY"] = gateway
    
    config_dict["network"]["HOST_MGMT_INTERFACE"] = mgmt_iface
    config_dict["network"]["HOST_DNS_SERVERS"] = "8.8.8.8,8.8.4.4"

    dns = config_dict["network"]["HOST_DNS_SERVERS"]

    if isinstance(dns, str):
        dns_list = [ip.strip() for ip in dns.split(",") if ip.strip()]
        config_dict["network"]["HOST_DNS_SERVERS"] = dns_list

    # Neutron
    config_dict.setdefault("neutron", {})
    config_dict["neutron"]["public_network"]["PUBLIC_SUBNET_CIDR"] = network

    config_dict["neutron"]["public_network"]["PUBLIC_SUBNET_RANGE_START"] = start_ip
    config_dict["neutron"]["public_network"]["PUBLIC_SUBNET_RANGE_END"] = last_ip
    config_dict["neutron"]["public_network"]["PUBLIC_SUBNET_GATEWAY"] = gateway
    config_dict["neutron"]["public_network"]["PUBLIC_SUBNET_DNS_SERVERS"] = dns_list
    
    config_dict["neutron"]["DRIVER"] = neutron_driver

    # Neutron OVS / OVN
    config_dict["neutron"].setdefault("ovs", {})
    config_dict["neutron"]["ovs"]["CREATE_BRIDGES"] = "yes" if neutron_driver == "ovs" else ""
    config_dict["neutron"]["ovs"]["PUBLIC_BRIDGE_INTERFACE"] = iface if neutron_driver == "ovs" else ""
    config_dict["neutron"]["ovs"]["PUBLIC_BRIDGE"] = "br-ex" if neutron_driver == "ovs" else ""
    config_dict["neutron"]["ovs"]["TENANT_BRIDGE"] = "br-tenant" if neutron_driver == "ovs" else ""
    config_dict["neutron"]["ovs"]["TUNNEL_BRIDGE"] = "br-tun" if neutron_driver == "ovs" else ""

    config_dict["neutron"].setdefault("ovn", {})
    if neutron_driver == "ovn":
        config_dict["neutron"]["ovn"].update({
            "CREATE_BRIDGES": "yes",
            "OVN_NB_PORT": 6641,
            "OVN_SB_PORT": 6642,
            "OVN_PUBLIC_BRIDGE_INTERFACE": iface,
            "OVN_PUBLIC_BRIDGE": "br-ex",
            "OVN_ENCAP_TYPE": "geneve",
            "OVN_L3_SCHEDULER": "leastloaded",
            "ENABLE_DISTRIBUTED_FLOATING_IP": False
        })
    else:
        config_dict["neutron"]["ovn"].update({
            "CREATE_BRIDGES": "",
            "OVN_NB_PORT": "",
            "OVN_SB_PORT": "",
            "OVN_PUBLIC_BRIDGE_INTERFACE": "",
            "OVN_PUBLIC_BRIDGE": "",
            "OVN_ENCAP_TYPE": "",
            "OVN_L3_SCHEDULER": "",
            "ENABLE_DISTRIBUTED_FLOATING_IP": ""
        })

    # Tenant network
    config_dict["neutron"].setdefault("tenant_network", {})
    config_dict["neutron"]["tenant_network"]["TYPE"] = "geneve" if neutron_driver == "ovn" else "flat"
    config_dict["neutron"]["tenant_network"]["VNI_RANGE"] = "1:65536" if neutron_driver == "ovn" else ""

    config_dict["neutron"]["default_security_group"]["defaults"]["remote_ip_prefix"] = network

    # Provider networks
    if neutron_driver == "ovs":
        config_dict["neutron"]["provider_networks"] = [
            {"name": "public", "bridge": "br-ex", "type": "flat"},
            {"name": "internal", "bridge": "br-tenant", "type": "flat"}
        ]
    else:
        config_dict["neutron"]["provider_networks"] = [
            {"name": "public", "bridge": "br-ex", "type": "flat"}
        ]

    # Cinder
    config_dict.setdefault("cinder", {})
    config_dict.setdefault("optional_services", {})

    if "cinder" not in config_dict:
        config_dict["cinder"] = {}

    config_dict["optional_services"]["INSTALL_MANILA"] = install_manila.lower()
    config_dict["optional_services"]["INSTALL_CINDER"] = install_cinder.lower()
    config_dict["optional_services"]["INSTALL_HORIZON"] = install_horizon.lower()

    if install_manila.lower() == "yes" and install_cinder.lower() == "yes":
        cinder_loop, manila_loop = get_free_loops(count=2)
    elif install_manila.lower() == "yes" and install_cinder.lower() == "no":
        manila_loop = get_free_loops(count=1)[0]
    elif install_manila.lower() == "no" and install_cinder.lower() == "yes":
        if cinder_lvm_image_size_in_gb is None:
            cinder_lvm_image_size_in_gb = 5
            
        cinder_loop = get_free_loops(count=1)[0]

    if install_cinder.lower() == "yes":

        config_dict["cinder"]["ENABLED_BACKENDS"] = cinder_enabled_backends or []
        config_dict["cinder"]["backends"] = {}

        if enable_cinder_backup.lower() == "yes":
            config_dict["cinder"]["ENABLE_CINDER_BACKUP"] = "yes"

            config_dict["cinder"]["backup"]["DRIVER"] = cinder_backup_driver

            if cinder_backup_driver == "posix":
                config_dict["cinder"]["backup"]["drivers"]["posix"]["BACKUP_PATH"] = "/var/lib/cinder/backups"
                
                config_dict["cinder"]["backup"]["drivers"].pop("nfs", None)
            elif cinder_backup_driver == "nfs":
                config_dict["cinder"]["backup"]["drivers"]["nfs"]["NFS_SHARE"] = f"{mgmt_ip}:/export/cinder-backups"
                config_dict["cinder"]["backup"]["drivers"]["nfs"]["MOUNT_POINT_BASE"] = "/var/lib/cinder/cinder/backup" 

                config_dict["cinder"]["backup"]["drivers"].pop("posix", None)

            config_dict["cinder"]["backup"]["COMPRESSION_ALGORITHM"] = compression_algorithm

            config_dict["cinder"]["backup"]["BACKUP_FILE_SIZE"] = backup_file_size_in_bytes
            config_dict["cinder"]["backup"]["BACKUP_SHA_BLOCK_SIZE_BYTES"] = backup_sha_block_size_in_bytes

            config_dict["cinder"]["backup"]["BACKUP_WORKERS"] = backup_workers
        else:
            config_dict["cinder"]["ENABLE_CINDER_BACKUP"] = "no"
            config_dict["cinder"].pop("backup", None)
        
        if "lvm" in cinder_enabled_backends :
            if not cinder_physical_volume or cinder_physical_volume.strip() == "":
                config_dict["cinder"]["backends"]["lvm"] = {
                    "BACKEND_NAME": "lvm",
                    "VOLUME_TYPE_NAME": "iscsi",
                    "CINDER_VOLUME_LVM_PHYSICAL_PV_LOOP_PATH": str(cinder_loop),
                    "CINDER_VOLUME_LVM_IMAGE_FILE_PATH": "/var/lib/cinder/images/cinder-volumes.img",
                    "CINDER_VOLUME_LVM_IMAGE_SIZE_IN_GB": cinder_lvm_image_size_in_gb,
                    "VOLUME_GROUP": cinder_lvm_vg,
                    "TARGET_IP_ADDRESS": mgmt_ip,
                    "VOLUME_CLEAR": "zero",
                    "VOLUME_CLEAR_SIZE": 1
                }
            else:
                config_dict["cinder"]["backends"]["lvm"] = {
                    "PHYSICAL_VOLUME": cinder_physical_volume,
                    "VOLUME_GROUP": cinder_lvm_vg,
                }

        if "nfs" in cinder_enabled_backends:
            config_dict["cinder"]["backends"]["nfs"] = {
                "BACKEND_NAME": "nfs",
                "VOLUME_TYPE_NAME": "nfs",
                "USE_EXTERNAL_SHARE": "no",
                "NFS_SHARE": f"{mgmt_ip}:/export/cinder-volumes",
                "MOUNT_POINT_BASE": "/var/lib/cinder/mnt",
                "NFS_SPARSED_VOLUMES": "sparse",
                "NFS_USED_RATIO": 0.95,
                "NFS_OVERSUB_RATIO": 1.0,
                "ENABLE_SNAPSHOTS": cinder_nfs_snapshots_enabled
            }

    if install_manila.lower() == "yes":

        share_helpers = []
        shares = []

        service_networks = []

        config_dict["manila"]["CREATE_SHARES"] = "yes"
        config_dict["manila"]["BACKEND"] = manila_backend
        config_dict["manila"]["SHARE_PROTOCOLS"] = [
            protocol.upper() for protocol in manila_share_protocols
        ]

        for protocol in manila_share_protocols:
            if protocol.lower() == "nfs":
                share_helpers.append({
                    "NFS": {
                        "name": "manila.share.drivers.helpers.NFSHelper"
                    }
                })

                if manila_backend.lower() == "generic":
                    shares.append({
                        "name": "default_nfs_internal_share",
                        "share_protocol": "NFS",
                        "share_size": 1,
                        "share_type": "default_share_type",
                        "is_public": "no",
                        "share_network": "manila_internal_service_network",
                        "access_rules": [
                            {
                                "access": "10.0.0.0/24",
                                "level": "rw",
                                "type": "ip",
                            },
                            {
                                "access": network,
                                "level": "rw",
                                "type": "ip",
                            },
                            {
                                "access": "10.254.0.0/28",
                                "level": "rw",
                                "type": "ip",
                            },
                        ]})

                    shares.append({
                        "name": "default_nfs_public_share",
                        "share_protocol": "NFS",
                        "share_size": 1,
                        "share_type": "default_share_type",
                        "is_public": "no",
                        "share_network": "manila_public_service_network",
                        "access_rules": [
                            {
                                "access": "10.0.0.0/24",
                                "level": "rw",
                                "type": "ip",
                            },
                            {
                                "access": network,
                                "level": "rw",
                                "type": "ip",
                            },
                            {
                                "access": "10.254.0.0/28",
                                "level": "rw",
                                "type": "ip",
                            },
                        ],
                    })

                elif manila_backend.lower() == "lvm":
                     shares.append({
                        "name": "default_nfs_share",
                        "share_protocol": "NFS",
                        "share_size": 1,
                        "share_type": "default_share_type",
                        "is_public": "no",
                        "access_rules": [
                            {
                                "access": "10.0.0.0/24",
                                "level": "rw",
                                "type": "ip",
                            },
                            {
                                "access": "172.20.10.0/24",
                                "level": "rw",
                                "type": "ip",
                            },
                        ]})

            elif protocol.lower() == "cifs":
                share_helpers.append({
                    "CIFS": {
                        "name": "manila.share.drivers.helpers.NASHelper"
                    }
                })

                if manila_backend.lower() == "generic":

                    service_networks.append([
                        {
                            "name": "manila_internal_service_network",
                            "neutron_network": "internal",
                        },
                        {
                            "name": "manila_public_service_network",
                            "neutron_network": "public",
                        }
                    ])
                     
                    shares.append({
                        "name": "default_cifs_internal_share",
                        "share_protocol": "CIFS",
                        "share_size": 1,
                        "share_type": "default_share_type",
                        "is_public": "no",
                        "share_network": "manila_internal_service_network",
                        "access_rules": [
                            {
                                "access": "10.0.0.0/24",
                                "level": "rw",
                                "type": "ip",
                            },
                            {
                                "access": network,
                                "level": "rw",
                                "type": "ip",
                            },
                            {
                                "access": "10.254.0.0/28",
                                "level": "rw",
                                "type": "ip",
                            },
                        ]})

                    shares.append({
                        "name": "default_cifs_public_share",
                        "share_protocol": "CIFS",
                        "share_size": 1,
                        "share_type": "default_share_type",
                        "is_public": "no",
                        "share_network": "manila_public_service_network",
                        "access_rules": [
                            {
                                "access": "10.0.0.0/24",
                                "level": "rw",
                                "type": "ip",
                            },
                            {
                                "access": network,
                                "level": "rw",
                                "type": "ip",
                            },
                            {
                                "access": "10.254.0.0/28",
                                "level": "rw",
                                "type": "ip",
                            },
                        ],
                    })

                elif manila_backend.lower() == "lvm":
                    shares.append({
                        "name": "default_cifs_share",
                        "share_protocol": "CIFS",
                        "share_size": 1,
                        "share_type": "default_share_type",
                        "is_public": "no",
                        "access_rules": [
                            {
                                "access": "samba-user",
                                "level": "rw",
                                "type": "user",
                            },
                        ],
                    })

        config_dict["manila"]["SHARE_HELPERS"] = share_helpers
        config_dict["manila"]["shares"] = shares

        if manila_backend.lower() == "generic":

            config_dict["manila"]["share_types"] = [{
                        "name": "default_share_type",
                        "is_public": "yes",
                        "extra_specs": [{
                                "driver_handles_share_servers": "yes"
                        }]
                }]

            config_dict["manila"]["backends"].pop("lvm", None)
        elif manila_backend.lower() == "lvm":
            config_dict["manila"]["share_types"] = [{
                                    "name": "default_share_type",
                                    "is_public": "yes",
                                    "extra_specs": [{
                                            "driver_handles_share_servers": "no"
                                    }]
                            }]

            config_dict["manila"]["backends"]["lvm"].update({
                "SHARE_EXPORT_IP": "172.20.10.10",   
                })

            for protocol in manila_share_protocols:
                if protocol.lower() in "cifs":
                    config_dict["manila"]["backends"]["lvm"].setdefault("samba", {})
                    config_dict["manila"]["backends"]["lvm"]["samba"]["SAMBA_SERVER_USER"] = "samba-user"
                    config_dict["manila"]["backends"]["lvm"]["samba"]["SAMBA_SERVER_USER_PASSWORD"] = generate_password()
                else:
                    config_dict["manila"]["backends"]["lvm"].pop("samba", None)

            config_dict["manila"]["backends"]["lvm"]["storage"]["SHARE_VOLUME_GROUP"] = manila_lvm_vg
            
            if not manila_lvm_physical_volume:
                config_dict["manila"]["backends"]["lvm"]["storage"].update({
                    "MANILA_LVM_IMAGE_FILE_PATH": "/var/lib/manila/images/manila-shares.img",
                    "MANILA_LVM_IMAGE_SIZE_IN_GB": manila_lvm_image_size_in_gb,
                    "MANILA_LVM_LOOP_PATH": str(manila_loop),
                })

            else:
                config_dict["manila"]["backends"]["lvm"]["storage"]["PHYSICAL_VOLUME"] = manila_lvm_physical_volume

                for key in (
                    "MANILA_LVM_IMAGE_FILE_PATH",
                    "MANILA_LVM_IMAGE_SIZE_IN_GB",
                    "MANILA_LVM_LOOP_PATH",
                ):
                    config_dict["manila"]["backends"]["lvm"]["storage"].pop(key, None)


            config_dict["manila"]["backends"].pop("generic", None)
    else:
        config_dict.pop("manila", None)

    # Compute
    config_dict.setdefault("compute", {})
    config_dict["compute"]["NOVA_COMPUTE_VIRT_TYPE"] = virt_type
    config_dict["compute"]["CPU_ALLOCATION_RATIO"] = 4.0
    config_dict["compute"]["RAM_ALLOCATION_RATIO"] = 1.5
    config_dict["compute"]["DISK_ALLOCATION_RATIO"] = 1.5

    # OpenStack
    config_dict.setdefault("openstack", {})
    config_dict["openstack"]["OPENSTACK_RELEASE"] = os_release.lower()
    config_dict["openstack"].setdefault("REGION_NAME", "RegionOne")

    with open(config_file_path, "w") as f:
        yaml.dump(_remove_empty(config_dict), f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    