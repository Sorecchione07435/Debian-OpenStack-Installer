import shutil
import os
import re
import ipaddress
import validators

from ipaddress import ip_address, ip_network

from .helpers import interface_exists, validate_ip, validate_cidr, is_loop_device, is_safe_lvm_device, validate_positive_int, is_valid_path, validate_port, is_valid_nfs_options, is_valid_nfs_share, ALLOWED_NFS_OPTIONS, get_parent_disk, get_device_for_path, get_physical_disk, get_vg_physical_disks
from ..core import colors
from .parser import get

from ...utils.config.helpers import parse_bool, prohibited_pw_chars

OPENSTACK_RESERVED_PORTS = {
    5000,   # Keystone
    5672,   # RabbitMQ
    3306,
    6080,   # Nova VNC
    8774,   # Nova
    #8776,   # Cinder
    #8777,   # Ceilometer
    9292,   # Glance
    9696,   # Neutron
    #8786,   # Manila
    80
}

# --- Passwords ---
def validate_passwords(config) -> bool:
    ok = True
    required = ["ADMIN_PASSWORD", "SERVICE_PASSWORD", "RABBITMQ_PASSWORD", "DATABASE_PASSWORD", "DEMO_PASSWORD"]
    for key in required:
        value = get(config, f"passwords.{key}")
        if not value:
            print(f"{colors.RED}Error: passwords.{key} is not set{colors.RESET}")
            ok = False

        if " " in value:
            print(f"{colors.RED}Error: '{required}' password invalid: contains spaces{colors.RESET}")
            ok = False
        elif any(c in value for c in prohibited_pw_chars):
            print(f"{colors.RED}Error: '{required}' password invalid: contains illegal characters{colors.RESET}")
            ok = False

    return ok

# --- Public network ---
def validate_host_network(config) -> bool:
    ok = True

    host_network_fields = [
        "network.HOST_IP",
        "network.HOST_IP_NETMASK",
    ]

    cidr_fields = ["network.HOST_IP_CIDR"]

    for field in cidr_fields:
        value = get(config, field)
        if not value:
            ok = False
            print(f"{colors.RED}Error: Field '{field}' is missing.{colors.RESET}")
        elif not validate_cidr(value, field):
            ok = False
            print(f"{colors.RED}Error: Field '{field}' has invalid CIDR: {value}{colors.RESET}")

    for field in host_network_fields:
        value = get(config, field)
        if not value:
            ok = False
            print(f"{colors.RED}Error: Field '{field}' is missing.{colors.RESET}")
        elif not validate_ip(value, field):
            ok = False

    dns_list = get(config, "network.HOST_DNS_SERVERS")
    if not dns_list:
        ok = False
        print(f"{colors.RED}Error: Field 'network.HOST_DNS_SERVERS' is missing.{colors.RESET}")
    else:
        for dns in dns_list:
            if not validate_ip(dns, "network.HOST_DNS_SERVERS"):
                ok = False

    host_domain = get(config, "network.HOST_DOMAIN") or ""

    if host_domain:
        if not validators.domain(host_domain):
            print(f"{colors.RED}Error: Field 'network.HOST_DOMAIN' as invalid domain name")
            ok = False

    return ok

def validate_public_network(config) -> bool:
    ok = True

    ip_fields = [
        "neutron.public_network.PUBLIC_SUBNET_GATEWAY",
        "neutron.public_network.PUBLIC_SUBNET_RANGE_START",
        "neutron.public_network.PUBLIC_SUBNET_RANGE_END",
    ]
    cidr_fields = ["neutron.public_network.PUBLIC_SUBNET_CIDR"]

    for field in cidr_fields:
        value = get(config, field)
        if not value:
            ok = False
            print(f"{colors.RED}Error: Field '{field}' is missing.{colors.RESET}")
        elif not validate_cidr(value, field):
            ok = False
            print(f"{colors.RED}Error: Field '{field}' has invalid CIDR: {value}{colors.RESET}")

    for field in ip_fields:
        value = get(config, field)
        if not value:
            ok = False
            print(f"{colors.RED}Error: Field '{field}' is missing.{colors.RESET}")
        elif not validate_ip(value, field):
            ok = False
            print(f"{colors.RED}Error: Field '{field}' has invalid IP: {value}{colors.RESET}")

    dns_servers = get(config, "neutron.public_network.PUBLIC_SUBNET_DNS_SERVERS", [])
    for i, dns in enumerate(dns_servers):
        if not validate_ip(dns, f"neutron.public_network.PUBLIC_SUBNET_DNS_SERVERS[{i}]"):
            ok = False
            print(f"{colors.RED}Error: DNS server at index {i} is invalid: {dns}{colors.RESET}")

    return ok

def validate_bridges(config, bridges):
    ok = True
    defined_bridges = set()

    public_bridge_iface = get(config, "neutron.ovn.OVN_PUBLIC_BRIDGE_INTERFACE") or get(config, "neutron.ovs.PUBLIC_BRIDGE_INTERFACE")

    for i, bridge in enumerate(bridges):
        name = bridge.get("name")
        port = bridge.get("port")

        if not name:
            print(f"{colors.RED}Error: bridge[{i}] missing 'name'{colors.RESET}")
            ok = False
            continue

        if not port:
            print(f"{colors.RED}Error: bridge '{name}' missing 'port'{colors.RESET}")
            ok = False
            continue

        if public_bridge_iface and public_bridge_iface in port:
            print(f"{colors.RED}Error: The public provider network bridge interface '{public_bridge_iface}' cannot be respecified in the neutron.bridges section.{colors.RESET}")
            ok = False

        defined_bridges.add(name)

    return ok, defined_bridges

def validate_provider_networks(config, provider_networks, defined_bridges):
    ok = True

    IGNORED_BRIDGES = []

    neutron_driver = (get(config, "neutron.DRIVER") or "").lower()

    seen_names = set()
    seen_bridges = {}

    public_bridge = (
        get(config, "neutron.ovn.OVN_PUBLIC_BRIDGE")
        or get(config, "neutron.ovs.PUBLIC_BRIDGE")
        or ""
    ).lower()

    IGNORED_BRIDGES.append(public_bridge)

    if neutron_driver == "ovs":
        TENANT_BRIDGE = get(config, "neutron.ovs.TENANT_BRIDGE")
        tunnel_bridge = get(config, "neutron.ovs.TUNNEL_BRIDGE")

        IGNORED_BRIDGES.append(tunnel_bridge)
        IGNORED_BRIDGES.append(TENANT_BRIDGE)

    for i, net in enumerate(provider_networks):
        net_name = net.get("name")

        if net_name in seen_names:
            print(f"{colors.RED}Error: duplicate provider network name '{net_name}'{colors.RESET}")
            ok = False

        seen_names.add(net_name)

        net_name = net.get("name")
        net_type = net.get("type")
        prefix = f"provider_networks[{i}] ('{net_name}')"

        subnet = net.get("subnet")

        bridge = net.get("bridge")
        if bridge and net_type == "flat":
            if bridge in seen_bridges:
                print(f"{colors.YELLOW}Warning: bridge '{bridge}' is used by both '{seen_bridges[bridge]}' and '{net_name}' — this may cause conflicts{colors.RESET}")
            else:
                seen_bridges[bridge] = net_name

        if not net_name:
            print(f"{colors.RED}Error: missing network name at index {i}{colors.RESET}")
            ok = False
            continue

        if net_type != "local":
            if not net.get("bridge"):
                print(f"{colors.RED}Error: {prefix} requires 'bridge' when type is '{net_type}'{colors.RESET}")
                ok = False

        if net_type not in ["flat", "vlan", "local"]:
            print(f"{colors.RED}Error: invalid type '{net_type}' in {prefix}{colors.RESET}")
            ok = False
            continue

        if net_type == "local" and net.get("bridge"):
            print(f"{colors.YELLOW}Warning: {prefix} has 'bridge' set but type is 'local' — bridge will be ignored{colors.RESET}")

        net_bridges = net.get("bridge", [])
        if isinstance(net_bridges, str):
            net_bridges = [net_bridges]

        bridge = net.get("bridge", "")
        if bridge.lower() in IGNORED_BRIDGES and subnet:
            print(f"{colors.YELLOW}Warning: {prefix} is mapped to a default bridge ('{bridge}') and has a 'subnet' section — it will be ignored{colors.RESET}")

        for b in net_bridges:
            if b not in IGNORED_BRIDGES and b not in defined_bridges:
                print(f"{colors.RED}Error: {prefix} references undefined bridge '{b}'{colors.RESET}")
                ok = False       
        
        if subnet:
            cidr = subnet.get("cidr")

            attach = subnet.get("attach_external_router") in (True, "yes", "true")
            is_ext = subnet.get("is_external") in (True, "yes", "true")

            if not cidr:
                print(f"{colors.RED}Error: {prefix} subnet missing 'cidr'{colors.RESET}")
                ok = False
            else:
                if not validate_cidr(cidr, f"{prefix} subnet.cidr"):
                    ok = False
                else:
                    net_obj = ipaddress.ip_network(cidr, strict=False)

                    gateway = subnet.get("gateway")
                    if gateway:
                        if not validate_ip(gateway, f"{prefix} subnet.gateway"):
                            ok = False
                        elif ipaddress.ip_address(gateway) not in net_obj:
                            print(f"{colors.RED}Error: {prefix} subnet.gateway '{gateway}' is not within '{cidr}'{colors.RESET}")
                            ok = False

                    net_range = subnet.get("range", {})
                    start = net_range.get("start")
                    end = net_range.get("end")

                    if bool(start) != bool(end):
                        print(f"{colors.RED}Error: {prefix} subnet.range requires both 'start' and 'end'{colors.RESET}")
                        ok = False

                    if start:
                        if not validate_ip(start, f"{prefix} subnet.range.start"):
                            ok = False
                        elif ipaddress.ip_address(start) not in net_obj:
                            print(f"{colors.RED}Error: {prefix} subnet.range.start '{start}' is not within '{cidr}'{colors.RESET}")
                            ok = False

                    if end:
                        if not validate_ip(end, f"{prefix} subnet.range.end"):
                            ok = False
                        elif ipaddress.ip_address(end) not in net_obj:
                            print(f"{colors.RED}Error: {prefix} subnet.range.end '{end}' is not within '{cidr}'{colors.RESET}")
                            ok = False

                    if attach and not is_ext:
                        print(f"{colors.RED}Error: {prefix} has 'attach_external_router: yes' but 'is_external' is not set{colors.RESET}")
                        ok = False

                    if start and end:
                        try:
                            if ipaddress.ip_address(start) >= ipaddress.ip_address(end):
                                print(f"{colors.RED}Error: {prefix} subnet.range.start must be less than range.end{colors.RESET}")
                                ok = False
                        except ValueError:
                            pass

                    for j, dns in enumerate(subnet.get("dns", [])):
                        if not validate_ip(dns, f"{prefix} subnet.dns[{j}]"):
                            ok = False

    return ok

def validate_default_security_group(config) -> bool:

    ok = True

    services_rules = get(config, "neutron.default_security_group.services", {})
    services_rules_remote_ip_prefix = get(config, "neutron.default_security_group.defaults.remote_ip_prefix")

    ALLOWED_PROTOCOLS = {"tcp", "udp", "icmp"}

    if not validate_cidr(services_rules_remote_ip_prefix, "neutron.default_security_group.defaults.remote_ip_prefix"):
        ok = False
        
    for name, rule in services_rules.items():

        if not rule.get("enabled"):
            continue

        protocol = (rule.get("protocol") or "tcp").lower()
        port = rule.get("port")

        if protocol not in ALLOWED_PROTOCOLS:
            print(f"{colors.RED}Error: Invalid protocol in {name}: {protocol}{colors.RESET}")
            ok = False

        if protocol == "icmp":
            if port is not None:
                print(f"{colors.RED}Error: ICMP cannot have port: {name}{colors.RESET}")
                ok = False
            continue

        if port is None:
            print(f"[{colors.RED}Error: Missing port for {name}{colors.RESET}")
            ok = False

        if not isinstance(port, int) or not (1 <= port <= 65535):
            print(f"{colors.RED}Error: Invalid port in {name}: {port}{colors.RESET}")
            ok = False

        if not isinstance(rule.get("enabled"), bool):
            print(f"{colors.RED}Error: enabled must be boolean in {name}{colors.RESET}")
            ok = False

    return ok

# --- Neutron ---
def validate_neutron(config) -> bool:
    ok = True

    neutron_driver = (get(config, "neutron.DRIVER") or "").lower()
    tenant_type = (get(config, "neutron.tenant_network.TYPE") or "").lower()
    ovs_create_bridges = get(config, "neutron.ovs.CREATE_BRIDGES")
    public_bridge_interface_ovs = get(config, "neutron.ovs.PUBLIC_BRIDGE_INTERFACE")
    ovn_encap_type = (get(config, "neutron.ovn.OVN_ENCAP_TYPE") or "").lower()

    provider_networks = get(config, "neutron.provider_networks", [])
    bridges = get(config, "neutron.bridges", [])

    if neutron_driver not in ("ovs", "ovn"):
        print(f"{colors.RED}Error: neutron.DRIVER must be 'ovs' or 'ovn' (got '{neutron_driver}'){colors.RESET}")
        ok = False

    # ==========================
    # OVS
    # ==========================
    if neutron_driver == "ovs":
        ovs_fields = [
            "neutron.ovs.PUBLIC_BRIDGE",
            "neutron.ovs.TENANT_BRIDGE",
            "neutron.ovs.PUBLIC_BRIDGE_INTERFACE"
        ]
        if tenant_type == "vxlan":
            ovs_fields.append("neutron.ovs.TUNNEL_BRIDGE")

        for field in ovs_fields:
            value = get(config, field)
            if not value:
                print(f"{colors.RED}Error: '{field}' is not set{colors.RESET}")
                ok = False

        if ovs_create_bridges not in ("yes", "no"):
            print(f"{colors.RED}Error: 'neutron.ovs.CREATE_BRIDGES' must be 'yes' or 'no' (got '{ovs_create_bridges}'){colors.RESET}")
            ok = False

        if public_bridge_interface_ovs and not interface_exists(public_bridge_interface_ovs):
            print(f"{colors.RED}The interface '{public_bridge_interface_ovs}' specified in neutron.ovs.PUBLIC_BRIDGE_INTERFACE does not exist.{colors.RESET}")
            ok = False

        if tenant_type == "geneve":
            print(f"{colors.RED}Error: neutron.tenant_network.TYPE 'geneve' is not supported by OVS{colors.RESET}")
            ok = False

        if tenant_type == "vxlan":
            vni_range = (get(config, "neutron.tenant_network.VNI_RANGE") or "").lower()
            if not vni_range:
                print(f"{colors.RED}Error: VNI_RANGE must be set for VXLAN tenant networks{colors.RESET}")
                ok = False

    # ==========================
    # OVN
    # ==========================
    if neutron_driver == "ovn":
        ovn_fields = [
            "neutron.ovn.OVN_PUBLIC_BRIDGE",
            "neutron.ovn.OVN_PUBLIC_BRIDGE_INTERFACE",
            "neutron.ovn.OVN_NB_PORT",
            "neutron.ovn.OVN_SB_PORT",
        ]
        for field in ovn_fields:
            value = get(config, field)
            if not value:
                print(f"{colors.RED}Error: '{field}' is not set{colors.RESET}")
                ok = False

        if ovn_encap_type and tenant_type and ovn_encap_type != tenant_type:
            print(f"{colors.RED}Error: OVN_ENCAP_TYPE ({ovn_encap_type}) does not match tenant network type ({tenant_type}).{colors.RESET}")
            ok = False

        if tenant_type not in ["geneve", "vxlan"]:
            print(f"{colors.RED}Error: Invalid tenant network type '{tenant_type}'{colors.RESET}")
            ok = False

        ovn_nb_port = validate_positive_int(
            get(config, ovn_fields[2]),
            ovn_fields[2]
        )

        ovn_sb_port = validate_positive_int(
            get(config, ovn_fields[3]),
            ovn_fields[3]
        )

        if ovn_nb_port is None:
            ok = False

        if ovn_sb_port is None:
            ok = False

        if ovn_nb_port is not None and not validate_port(ovn_nb_port):
            print(
                f"{colors.RED}Error: '{ovn_fields[2]}' contains an invalid port."
                f"{colors.RESET}"
            )
            ok = False

        if ovn_sb_port is not None and not validate_port(ovn_sb_port):
            print(
                f"{colors.RED}Error: '{ovn_fields[3]}' contains an invalid port."
                f"{colors.RESET}"
            )
            ok = False

        if ovn_nb_port in OPENSTACK_RESERVED_PORTS:
            print(
                f"{colors.RED}Error: '{ovn_fields[2]}' contains a reserved port."
                f"{colors.RESET}"
            )
            ok = False

        if ovn_sb_port in OPENSTACK_RESERVED_PORTS:
            print(
                f"{colors.RED}Error: '{ovn_fields[3]}' contains a reserved port."
                f"{colors.RESET}"
            )
            ok = False

    ok_bridges, defined_bridges = validate_bridges(config, bridges)
    ok_networks = validate_provider_networks(config, provider_networks, defined_bridges)
    ok_default_security_group = validate_default_security_group(config)

    ok &= ok_bridges
    ok &= ok_networks
    ok &= ok_default_security_group

    return ok

def validate_cinder_backup(config) -> bool:
    ok = True

    def parse_nfs_source(source):
        if not source or ":" not in source:
            return None, None
        server, _, export = source.partition(":")
        return server.strip(), export.strip()


    def get_nfs_share_for_path(path):
        check_path = path
        while check_path and check_path != "/" and not os.path.exists(check_path):
            check_path = os.path.dirname(check_path)

        source = get_device_for_path(check_path)
        return parse_nfs_source(source)

    backup_driver = (get(config, "cinder.backup.DRIVER") or "").strip().lower()

    enabled_cinder_backends = get(config, "cinder.ENABLED_BACKENDS", []) or []

    cinder_backup_fields = [
        "cinder.backup.DRIVER",
        "cinder.backup.COMPRESSION_ALGORITHM",
        "cinder.backup.BACKUP_FILE_SIZE",
        "cinder.backup.BACKUP_SHA_BLOCK_SIZE_BYTES",
        "cinder.backup.BACKUP_WORKERS",
    ]

    for field in cinder_backup_fields:
        if not get(config, field):
            print(f"{colors.RED}Error: '{field}' is not set{colors.RESET}")
            ok = False
    
    if backup_driver not in ("posix", "nfs"):
        print(
            f"{colors.RED}Error: Invalid value for 'cinder.backup.DRIVER' "
            f"Allowed values are: ('posix', 'nfs').{colors.RESET}"
        )
        ok = False

    if backup_driver == "posix":
        posix_backup = get(config, "cinder.backup.drivers.posix")
        posix_backup_path = get(config, "cinder.backup.drivers.posix.BACKUP_PATH")

        if not posix_backup:
            print(f"{colors.RED}Error: 'cinder.backup.drivers.posix' section is missing{colors.RESET}")
            ok = False

        if not posix_backup_path:
            print(f"{colors.RED}Error: 'cinder.backup.drivers.posix.BACKUP_PATH' is not set{colors.RESET}")
            ok = False

        if not is_valid_path(posix_backup_path, "cinder.backup.drivers.posix.BACKUP_PATH"):
            ok = False

        if posix_backup_path:
            if "lvm" in enabled_cinder_backends:
                vg_name = get(config, "cinder.backends.lvm.VOLUME_GROUP")

                backup_disk = get_physical_disk(get_device_for_path(posix_backup_path))
                vg_disks = get_vg_physical_disks(vg_name)

                if backup_disk and vg_disks:
                    if backup_disk in vg_disks:
                        print(
                            f"{colors.YELLOW}Warning: 'cinder.backup.drivers.posix.BACKUP_PATH' "
                            f"is located on the same physical disk ({backup_disk}) as the Cinder LVM "
                            f"volume group ('{vg_name}'). In the event of a disk failure, the backups "
                            f"will not be recoverable.{colors.RESET}"
                        )
                else:
                    print(
                        f"{colors.YELLOW}Warning: unable to determine if "
                        f"'cinder.backup.drivers.posix.BACKUP_PATH' shares the physical disk "
                        f"with the Cinder volume group ('{vg_name}').{colors.RESET}"
                    )

            if "nfs" in enabled_cinder_backends:
                nfs_backup_share = get(config, "cinder.backends.nfs.NFS_SHARE")

                backup_source = get_nfs_share_for_path(posix_backup_path)
                backup_server, backup_export = parse_nfs_source(backup_source)
                vol_server, vol_export = parse_nfs_source(nfs_backup_share)

                if backup_server and vol_server:
                    if backup_server == vol_server and backup_export == vol_export:
                        print(
                            f"{colors.YELLOW}Warning: 'cinder.backup.drivers.posix.BACKUP_PATH' "
                            f"is mounted from the same NFS export ({nfs_backup_share}) as the Cinder "
                            f"volume backend NFS. In the event of a storage failure, the "
                            f"backups will not be recoverable.{colors.RESET}"
                        )
                    elif backup_server == vol_server:
                        print(
                            f"{colors.YELLOW}Warning: 'cinder.backup.drivers.posix.BACKUP_PATH' "
                            f"is mounted from the same NFS server ({backup_server}) as the Cinder "
                            f"volume backend NFS, on a different export.{colors.RESET}"
                        )

    elif backup_driver == "nfs":
        
        nfs_backup = get(config, "cinder.backup.drivers.nfs")
        nfs_backup_fields = [
            "cinder.backup.drivers.nfs.NFS_SHARE",
            "cinder.backup.drivers.nfs.MOUNT_POINT_BASE",
            "cinder.backup.drivers.nfs.USE_EXTERNAL_SHARE"
        ]

        nfs_share = get(config, nfs_backup_fields[0])
        mount_point_base = get(config, nfs_backup_fields[1])

        use_external_share = get(config, nfs_backup_fields[2])

        nfs_extra_mount_options = get(config, "cinder.backup.drivers.nfs.MOUNT_OPTIONS")

        if not nfs_backup:
            print(f"{colors.RED}Error: 'cinder.backup.drivers.nfs' section is missing{colors.RESET}")
            ok = False

        for field in nfs_backup_fields:
            if not get(config, field):
                print(f"{colors.RED}Error: '{field}' is not set{colors.RESET}")
                ok = False

        if not is_valid_nfs_share(nfs_share):
            print(f"{colors.RED}Error: '{nfs_backup_fields[0]}' is an invalid NFS Share syntax{colors.RESET}")
            ok = False

        if not is_valid_path(mount_point_base, nfs_backup_fields[1]):
            ok = False

        if nfs_extra_mount_options:
            if not is_valid_nfs_options(nfs_extra_mount_options):
                print(f"{colors.RED}Error: 'cinder.backup.drivers.nfs.MOUNT_OPTIONS' contains invalid options\n\nThe valid options list are: {', '.join(ALLOWED_NFS_OPTIONS)}{colors.RESET}")
                ok = False

        if use_external_share:
            if use_external_share not in ("yes", "no", "true", "false"):
                print(f"{colors.RED}Error: '{nfs_backup_fields[2]}' must be yes/no{colors.RESET}")
                ok = False

        if "nfs" in enabled_cinder_backends:
            backup_server, backup_export = parse_nfs_source(nfs_share)

            vol_nfs_share = get(config, f"cinder.backends.nfs.NFS_SHARE")
            vol_server, vol_export = parse_nfs_source(vol_nfs_share)

            if backup_server and vol_server:
                if backup_server == vol_server and backup_export == vol_export:
                    print(
                        f"{colors.YELLOW}Warning: 'cinder.backup.drivers.nfs.NFS_SHARE' "
                        f"points to the same NFS export ({nfs_share}) as the Cinder volume "
                        f"backend NFS. In the event of a storage failure, the backups "
                        f"will not be recoverable.{colors.RESET}"
                    )
                elif backup_server == vol_server:
                    print(
                        f"{colors.YELLOW}Warning: 'cinder.backup.drivers.nfs.NFS_SHARE' uses the "
                        f"same NFS server ({backup_server}) as the Cinder volume backend "
                        f"NFS, on a different export.{colors.RESET}"
                    )

    fields_to_validate_int = [
        cinder_backup_fields[2],
        cinder_backup_fields[3],
        cinder_backup_fields[4]
    ]

    for int_field in fields_to_validate_int:
        value = get(config, int_field)

        if validate_positive_int(value, int_field) is None:
            ok = False

    return ok

# --- Cinder ---
def validate_cinder(config) -> bool:
    ok = True

    cinder_config = get(config, "cinder")

    enable_cinder_backup = get(config, "cinder.ENABLE_CINDER_BACKUP")
    enabled_backends = get(config, "cinder.ENABLED_BACKENDS", []) or []

    allowed_backends = {"lvm", "nfs"}
    volume_type_names = []

    default_volume_type = (get(config, "cinder.DEFAULT_VOLUME_TYPE" or "")).strip()

    OPENSTACK_RESERVED_PORTS.add(8776)

    if not isinstance(enabled_backends, list):
        print(f"{colors.RED}Error: 'cinder.ENABLED_BACKENDS' must be a list{colors.RESET}")
        ok = False

    if not all(isinstance(backend, str) and backend.strip() for backend in enabled_backends):
        print(f"{colors.RED}Error: 'cinder.ENABLED_BACKENDS' must contain non-empty strings{colors.RESET}")
        ok = False

    invalid = set(enabled_backends) - allowed_backends

    if invalid:
        print(f"{colors.RED}Error: Unsupported Cinder backends: '{invalid}'{colors.RESET}")
        ok = False

    if not default_volume_type:
        print(f"{colors.RED}Error: 'cinder.DEFAULT_VOLUME_TYPE' is not set{colors.RESET}")
        ok = False

    if "lvm" in enabled_backends:

        size_raw = (get(config, "cinder.backends.lvm.CINDER_VOLUME_LVM_IMAGE_SIZE_IN_GB") or "")
        path = (get(config, "cinder.backends.lvm.CINDER_VOLUME_LVM_IMAGE_FILE_PATH") or "").strip().lower()
        pv = (get(config, "cinder.backends.lvm.PHYSICAL_VOLUME") or "").strip().lower()
        volume_clear = (get(config, "cinder.backends.lvm.VOLUME_CLEAR") or "").lower()
        volume_clear_size = get(config, "cinder.backends.lvm.VOLUME_CLEAR_SIZE")

        volume_type_name = get(config, "cinder.backends.lvm.VOLUME_TYPE_NAME")

        size = None

        required_fields = [
            "cinder.backends.lvm.BACKEND_NAME",
            "cinder.backends.lvm.VOLUME_TYPE_NAME",
            "cinder.backends.lvm.VOLUME_GROUP",
            "cinder.backends.lvm.VOLUME_CLEAR",
            "cinder.backends.lvm.VOLUME_CLEAR_SIZE",
            "cinder.backends.lvm.TARGET_IP_ADDRESS"
        ]

        if not cinder_config:
            print(f"{colors.RED}Error: cinder section is missing{colors.RESET}")
            return False

        if enable_cinder_backup not in ("yes", "no"):
            print(f"{colors.RED}Error: 'cinder.ENABLE_CINDER_BACKUP' must be 'yes' or 'no'{colors.RESET}")
            ok = False

        volume_type_names.append(volume_type_name)

        for field in required_fields:
            if not get(config, field):
                print(f"{colors.RED}Error: '{field}' is not set{colors.RESET}")
                ok = False
                
        if pv:
            if not os.path.exists(pv):
                print(f"{colors.RED}Error: PHYSICAL_VOLUME '{pv}' does not exist{colors.RESET}")
                ok = False
                return False

            if not pv.startswith("/dev/") or pv.startswith("/dev/loop") or is_loop_device(pv):
                print(f"{colors.RED}Error: loop devices are not allowed as Physical Volume ({pv}){colors.RESET}")
                ok = False
                return False
            
            if not is_safe_lvm_device(pv):
                print(f"{colors.RED}Error: Unsafe LVM device blocked for security: {pv}{colors.RESET}")
                ok = False
                return False
        
        else:
            required_loopback_fields = [
                "cinder.backends.lvm.CINDER_VOLUME_LVM_IMAGE_FILE_PATH",
                "cinder.backends.lvm.CINDER_VOLUME_LVM_IMAGE_SIZE_IN_GB",
                "cinder.backends.lvm.CINDER_VOLUME_LVM_PHYSICAL_PV_LOOP_PATH",
            ]

            for field in required_loopback_fields:
                if not get(config, field) :
                    print(f"{colors.RED}Error: '{field}' is not set{colors.RESET}")
                    ok = False

            cinder_volume_lvm_image_file_path = get(config, required_loopback_fields[0])
            cinder_lvm_loop_path = (get(config, required_loopback_fields[2]) or "").strip().lower()

            cinder_loopback_size_raw = validate_positive_int(size_raw, "cinder.backends.lvm.CINDER_VOLUME_LVM_IMAGE_SIZE_IN_GB")

            if not is_valid_path(cinder_volume_lvm_image_file_path, required_loopback_fields[0]):
                ok = False

            if not is_valid_path(cinder_lvm_loop_path, required_loopback_fields[2]):
                ok = False

            if cinder_loopback_size_raw is None:
                ok = False
            else:
                size = cinder_loopback_size_raw

            if cinder_lvm_loop_path:
                if not cinder_lvm_loop_path.startswith("/dev/loop"):
                    print(
                        f"{colors.RED}Error: '{required_loopback_fields[2]}' must be a loop device, "
                        f"found '{cinder_lvm_loop_path}'{colors.RESET}"
                    )
                    ok = False

            if path:
                directory = os.path.dirname(path) or "/"

                while not os.path.exists(directory):
                    parent = os.path.dirname(directory)
                    if parent == directory:
                        directory = "/"
                        break
                    directory = parent

                try:
                    _, _, free = shutil.disk_usage(directory)
                    free_gb = free / (1024**3)

                    if size is not None and size > free_gb:
                        print(
                            f"{colors.YELLOW}Warning: the requested Cinder LVM image size ({size} GB) exceeds "
                            f"the available disk space ({free_gb:.2f} GB). "
                            f"The sparse file will be created successfully, but the volume group may run out "
                            f"of space as volumes are written.{colors.RESET}"
                        )

                except FileNotFoundError:
                    print(f"{colors.RED}Error: cannot determine disk usage for {directory}{colors.RESET}")
                    ok = False

        if "nfs" in enabled_backends:

            volume_type_name = get(config, "cinder.backends.nfs.VOLUME_TYPE_NAME")

            required_fields = [
                "cinder.backends.nfs.BACKEND_NAME",
                "cinder.backends.nfs.VOLUME_TYPE_NAME",
                "cinder.backends.nfs.NFS_SHARE",
                "cinder.backends.nfs.MOUNT_POINT_BASE",
            ]

            ratios_fields = [
                "cinder.backends.nfs.NFS_USED_RATIO",
                "cinder.backends.nfs.NFS_OVERSUB_RATIO",
            ]

            for field in required_fields:
                if not get(config, field):
                    print(f"{colors.RED}Error: '{field}' is not set{colors.RESET}")
                    ok = False

            mount_options = get(config, "cinder.backends.nfs.MOUNT_OPTIONS")
            sparsed_volumes = get(config, "cinder.backends.nfs.NFS_SPARSED_VOLUMES")

            enabled_snapshots = get(config, "cinder.backends.nfs.ENABLE_SNAPSHOTS")

            use_external_share = get(config, "cinder.backends.nfs.USE_EXTERNAL_SHARE")

            nfs_share = get(config, required_fields[2])
            mount_point_base = get(config, required_fields[3])

            volume_type_names.append(volume_type_name)

            if not is_valid_nfs_share(nfs_share):
                print(f"{colors.RED}Error: '{required_fields[2]}' is an invalid NFS Share syntax{colors.RESET}")
                ok = False
            
            if not is_valid_path(mount_point_base, required_fields[3]):
                ok = False

            if mount_options:
                if not is_valid_nfs_options(mount_options):
                    print(f"{colors.RED}Error: 'cinder.backends.nfs.MOUNT_POINT_BASE' contains invalid options\n\nThe valid options list are: {', '.join(ALLOWED_NFS_OPTIONS)}{colors.RESET}")
                    ok = False

            if sparsed_volumes:
                if sparsed_volumes not in ("sparse", "thick"):
                    print(f"{colors.RED}Error: Invalid value for 'cinder.backends.nfs.NFS_SPARSED_VOLUMES'"
                            f"Allowed values are: ('sparse', 'thick').{colors.RESET}"
                    )
                    ok = False

            if use_external_share:
                if use_external_share not in ("yes", "no", "true", "false"):
                    print(f"{colors.RED}Error: 'cinder.backends.nfs.USE_EXTERNAL_SHARE' must be yes/no{colors.RESET}")
                    ok = False

            for ratio_field in ratios_fields:
                ratio = get(config, ratio_field, None)

                if ratio:
                    try:
                        float_val = float(ratio)

                        if not 0 <= float_val <= 1:
                            print(
                                f"{colors.RED}Error: "
                                f"'{ratio_field}' must be between 0 and 1, "
                                f"found: {ratio}{colors.RESET}"
                            )
                            ok = False

                    except (TypeError, ValueError):
                        print(
                            f"{colors.RED}Error: "
                            f"'{ratio_field}' must be a decimal number, "
                            f"found: {ratio}{colors.RESET}"
                        )
                        ok = False

            if enabled_snapshots:
                if enabled_snapshots not in ("yes", "no", "true", "false"):
                    print(f"{colors.RED}Error: 'cinder.backends.nfs.ENABLE_SNAPSHOTS' must be 'yes' or 'no'{colors.RESET}")
                    ok = False

        target_ip = get(config, "cinder.backends.lvm.TARGET_IP_ADDRESS") or ""

        if default_volume_type not in volume_type_names:
            print(
                f"{colors.RED}Error: default volume type "
                f"'{default_volume_type}' is not configured. "
                f"Available types: {', '.join(volume_type_names)}{colors.RESET}"
            )
            ok = False

        if isinstance(target_ip, dict) or (isinstance(target_ip, str) and "{network.HOST_IP}" in target_ip):
            pass  
        elif target_ip and isinstance(target_ip, str):
            if not validate_ip(target_ip, "cinder.backends.lvm.TARGET_IP_ADDRESS"):
                ok = False
        else:
            ok = False
            
        if volume_clear not in ("zero", "shred", "none"):
            print(
                f"{colors.RED}Error: Invalid value for 'cinder.volume_clear'. "
                f"Allowed values are: 'zero', 'shred', 'none'.{colors.RESET}"
            )
            ok = False

        if validate_positive_int(volume_clear_size, required_fields[2]) is None:
            ok = False

    if enable_cinder_backup == "yes":
        ok &= validate_cinder_backup(config)
    
    return ok

# --- Manila ---
def validate_manila(config) -> bool:
    ok = True

    valid_backends = [
            "lvm",
            "generic"
        ]
    
    valid_protocols = [
            "NFS",
            "CIFS"
        ]
    
    valid_helpers = {
            "NFS": "manila.share.drivers.helpers.NFSHelper",
            "CIFS": "manila.share.drivers.helpers.NASHelper",
        }

    manila_config = get(config, "manila")

    enabled_backend = (get(config, "manila.BACKEND") or "").lower()

    share_protocols = get(config, "manila.SHARE_PROTOCOLS") or []
    share_helpers = get(config, "manila.SHARE_HELPERS") or []

    share_types = get(config, "manila.share_types") or []
    shares = get(config, "manila.shares") or []

    create_shares = (get(config, "manila.CREATE_SHARES") or "").lower()

    OPENSTACK_RESERVED_PORTS.add(8786)

    if isinstance(share_protocols, str):
        share_protocols = [share_protocols]

    if isinstance(share_helpers, dict):
        share_helpers = [share_helpers]

    share_protocols = [p.upper() for p in share_protocols]

    if not manila_config:
        print(f"{colors.RED}Error: manila section is missing{colors.RESET}")
        return False

    if create_shares not in ("yes", "no"):
        print(f"{colors.RED}Error: 'manila.share_types' must be 'yes' or 'no' (got '{create_shares}'){colors.RESET}")
        ok = False

    if not enabled_backend:
        print(f"{colors.RED}Error: manila.backend is missing{colors.RESET}")
        ok = False

    if not create_shares:
        print(f"{colors.RED}Error: manila.CREATE_SHARES is missing{colors.RESET}")
        ok = False

    if not share_protocols:
        print(f"{colors.RED}Error: manila.SHARE_PROTOCOLS is empty{colors.RESET}")
        ok = False

    if not share_helpers:
        print(f"{colors.RED}Error: manila.SHARE_HELPERS is empty{colors.RESET}")
        ok = False

    if enabled_backend not in valid_backends:
        print(f"{colors.RED}Error: manila.backend '{enabled_backend}' "
        f"is not a valid backend{colors.RESET}"
    )
        ok = False

    if str(create_shares).lower() not in ("yes", "no", "true", "false"):
        print(f"{colors.RED}Error: invalid manila.CREATE_SHARES "
        f"must be yes/no{colors.RESET}")
        ok = False

    for protocol in share_protocols:
        if protocol not in valid_protocols:
            print(f"{colors.RED}Error: manila.SHARE_PROTOCOLS.{protocol} "
                f"is not a valid protocol type{colors.RESET}"
            )
            ok = False

    for helper in share_helpers:
        if not isinstance(helper, dict):
            print(f"{colors.RED}Error: invalid format for manila.SHARE_HELPERS entry '{helper}'{colors.RESET}")
            ok = False
            continue

        for helper_type, helper_config in helper.items():
            helper_type = helper_type.upper()
            helper_name = (helper_config or {}).get("name")

            if not helper_name:
                print(
                    f"{colors.RED}Error: manila.SHARE_HELPERS.{helper_type}.name "
                    f"is missing{colors.RESET}"
                )
                ok = False
                continue

            expected = valid_helpers.get(helper_type)

            if expected is None:
                print(f"{colors.RED}Error: manila.SHARE_HELPERS.{helper_type} "
                      f"is not a valid protocol type{colors.RESET}")
                
                ok = False
                continue

            if helper_name != expected:
                print(f"{colors.RED}Error: invalid helper for {helper_type}: "
                    f"expected '{expected}', got '{helper_name}'{colors.RESET}"
                )
                ok = False

    if enabled_backend == "lvm":

        size = None

        lvm_config = get(config, "manila.backends.lvm")

        lvm_physical_volume = (get(config, "manila.backends.lvm.storage.PHYSICAL_VOLUME") or "").strip().lower()

        lvm_backend_size_raw = (get(config, "manila.backends.lvm.storage.MANILA_LVM_IMAGE_SIZE_IN_GB") or "")
        lvm_backend_path = (get(config, "manila.backends.lvm.storage.MANILA_LVM_IMAGE_FILE_PATH") or "").strip().lower()

        lvm_share_driver = get(config, "manila.backends.lvm.SHARE_DRIVER") or ""
        lvm_share_export_ip = get(config, "manila.backends.lvm.SHARE_EXPORT_IP") or ""

        lvm_backend_fields = [
            "manila.backends.lvm.BACKEND_NAME",
            "manila.backends.lvm.SHARE_DRIVER",
            "manila.backends.lvm.storage.SHARE_VOLUME_GROUP",
            "manila.backends.lvm.SHARE_EXPORT_IP",
        ]

        for protocol in share_protocols:
            if "CIFS" in protocol:
                samba_fields = [
                    "manila.backends.lvm.samba.SAMBA_SERVER_USER",
                    "manila.backends.lvm.samba.SAMBA_SERVER_USER_PASSWORD",
                ]

                for field in samba_fields:
                    if not get(config, field) :
                        print(f"{colors.RED}Error: '{field}' is not set{colors.RESET}")
                        ok = False

                samba_password = get(config, "manila.backends.lvm.samba.SAMBA_SERVER_USER_PASSWORD")

                if " " in samba_password:
                    print(f"{colors.RED}Error: 'manila.backends.lvm.samba.SAMBA_SERVER_USER_PASSWORD' password invalid: contains spaces{colors.RESET}")
                    ok = False
                elif any(c in samba_password for c in prohibited_pw_chars):
                    print(f"{colors.RED}Error: 'manila.backends.lvm.samba.SAMBA_SERVER_USER_PASSWORD' password invalid: contains illegal characters{colors.RESET}")
                    ok = False

        if not lvm_config:
            print(f"{colors.RED}Error: manila.backends.lvm section is missing{colors.RESET}")
            return False

        for field in lvm_backend_fields:
            if not get(config, field) :
                print(f"{colors.RED}Error: '{field}' is not set{colors.RESET}")
                ok = False

        if lvm_physical_volume:
            if not os.path.exists(lvm_physical_volume):
                print(f"{colors.RED}Error: PHYSICAL_VOLUME '{lvm_physical_volume}' does not exist{colors.RESET}")
                ok = False
            else:
                if (
                    not lvm_physical_volume.startswith("/dev/")
                    or lvm_physical_volume.startswith("/dev/loop")
                    or is_loop_device(lvm_physical_volume)
                ):
                    print(f"{colors.RED}Error: loop devices are not allowed as Physical Volume "
                        f"({lvm_physical_volume}){colors.RESET}")
                    ok = False
                elif not is_safe_lvm_device(lvm_physical_volume):
                    print(f"{colors.RED}Error: Unsafe LVM device blocked for security: "
                        f"{lvm_physical_volume}{colors.RESET}")
                    ok = False
        else:
            if lvm_backend_size_raw:

                loopback_lvm_size = validate_positive_int(lvm_backend_size_raw, "manila.backends.lvm.storage.MANILA_LVM_IMAGE_SIZE_IN_GB")

                if loopback_lvm_size is None:
                    ok = False
                else:
                    size = loopback_lvm_size

            required_lvm_loopback_fields = [
                "manila.backends.lvm.storage.MANILA_LVM_IMAGE_FILE_PATH",
                "manila.backends.lvm.storage.MANILA_LVM_IMAGE_SIZE_IN_GB",
                "manila.backends.lvm.storage.MANILA_LVM_LOOP_PATH",
            ]

            for field in required_lvm_loopback_fields:
                if not get(config, field) :
                    print(f"{colors.RED}Error: '{field}' is not set{colors.RESET}")
                    ok = False

            lvm_loop_image_path = (get(config, "manila.backends.lvm.storage.MANILA_LVM_IMAGE_FILE_PATH") or "").lower()
            lvm_loop_path = (get(config, "manila.backends.lvm.storage.MANILA_LVM_LOOP_PATH") or "").lower()

            if not is_valid_path(lvm_loop_image_path, required_lvm_loopback_fields[0]):
                ok = False

            if not is_valid_path(lvm_loop_path, required_lvm_loopback_fields[2]):
                ok = False

            if lvm_loop_path and not lvm_loop_path.startswith("/dev/loop"):
                print(f"{colors.RED}Error: MANILA_LVM_LOOP_PATH must be a loop device, "
                    f"found '{lvm_loop_path}'{colors.RESET}")
                ok = False

            if lvm_backend_path:
                directory = os.path.dirname(lvm_backend_path) or "/"
    
                while not os.path.exists(directory):
                    parent = os.path.dirname(directory)
                    if parent == directory:
                        directory = "/"
                        break
                    directory = parent
    
                try:
                    _, _, free = shutil.disk_usage(directory)
                    free_gb = free / (1024**3)
    
                    if size is not None and size > free_gb:
                        print(
                            f"{colors.YELLOW}Warning: the requested Manila LVM image size ({size} GB) exceeds "
                            f"the available disk space ({free_gb:.2f} GB). "
                            f"The sparse file will be created successfully, but the volume group may run out "
                            f"of space as volumes are written.{colors.RESET}"
                        )
    
                except FileNotFoundError:
                    print(f"{colors.RED}Error: cannot determine disk usage for {directory}{colors.RESET}")
                    ok = False
    
        if lvm_share_driver != "manila.share.drivers.lvm.LVMShareDriver":
            print(f"{colors.RED}Error: invalid LVM share driver '{lvm_share_driver}'. "
                f"Expected 'manila.share.drivers.lvm.LVMShareDriver'{colors.RESET}")
            ok = False

        if validate_ip(lvm_share_export_ip, "manila.backends.lvm.SHARE_EXPORT_IP"):
            share_export_ip = ipaddress.ip_address(lvm_share_export_ip)

            if not share_export_ip.is_private:
                print(
                    f"{colors.RED}Error: IP address '{share_export_ip}' "
                    f"in manila.backends.lvm.SHARE_EXPORT_IP is not a private address. "
                    f"A private IP address is required{colors.RESET}"
                )
                ok = False
        else:
            ok = False

    elif enabled_backend == "generic":

        include_cinder = parse_bool(get(config, "optional_services.INSTALL_CINDER", False))

        generic_config = get(config, "manila.backends.generic")

        generic_service_flavor_id = (get(config, "manila.backends.generic.SERVICE_INSTANCE_FLAVOR.ID") or "")
        generic_service_flavor_ram = (get(config, "manila.backends.generic.SERVICE_INSTANCE_FLAVOR.RAM") or "")
        generic_service_flavor_vcpus = (get(config, "manila.backends.generic.SERVICE_INSTANCE_FLAVOR.VCPUS") or "")
        generic_service_flavor_disk = (get(config, "manila.backends.generic.SERVICE_INSTANCE_FLAVOR.DISK") or "")

        connect_share_driver_to_tenant_network = (get(config, "manila.backends.generic.CONNECT_SHARE_SERVER_TO_TENANT_NETWORK") or "")
        provider_networks = get(config, "neutron.provider_networks") or []

        service_networks = get(config, "manila.backends.generic.service_networks") or []

        generic_backend_fields = [
            "manila.backends.generic.BACKEND_NAME",
            "manila.backends.generic.SERVICE_IMAGE_NAME",
            "manila.backends.generic.INTERFACE_DRIVER",
            "manila.backends.generic.CONNECT_SHARE_SERVER_TO_TENANT_NETWORK",
            "manila.backends.generic.SERVICE_INSTANCE_FLAVOR.NAME",
            "manila.backends.generic.SERVICE_INSTANCE_FLAVOR.ID",
            "manila.backends.generic.SERVICE_INSTANCE_FLAVOR.RAM",
            "manila.backends.generic.SERVICE_INSTANCE_FLAVOR.VCPUS",
            "manila.backends.generic.SERVICE_INSTANCE_FLAVOR.DISK",
        ]

        if not generic_config:
            print(f"{colors.RED}Error: manila.backends.generic section is missing{colors.RESET}")
            ok = False

        for field in generic_backend_fields:
            if not get(config, field) :
                print(f"{colors.RED}Error: '{field}' is not set{colors.RESET}")
                ok = False

        if not include_cinder:
            print(f"{colors.RED}Error: Manila Generic backend requires Cinder service to be enabled{colors.RESET}")
            return False

        if connect_share_driver_to_tenant_network not in ("yes", "no"):
            print(f"{colors.RED}Error: 'manila.backends.generic.CONNECT_SHARE_SERVER_TO_TENANT_NETWORK' must be 'yes' or 'no' (got '{connect_share_driver_to_tenant_network}'){colors.RESET}")
            ok = False

        if not isinstance(service_networks, list):
            print(f"{colors.RED}Error: manila.backends.generic.service_networks must be a list{colors.RESET}")
            ok = False

        if not service_networks:
            print(f"{colors.RED}Error: manila.backends.generic.service_networks is empty{colors.RESET}")
            ok = False

        provider_network_names = set()
        allowed_neutron_networks = {"internal"}

        if not isinstance(provider_networks, list):
            print(f"{colors.RED}Error: neutron.provider_networks must be a list{colors.RESET}")
            ok = False
        else:
            for provider in provider_networks:
                if not isinstance(provider, dict):
                    print(f"{colors.RED}Error: invalid neutron.provider_networks entry '{provider}'{colors.RESET}")
                    ok = False
                    continue

                name = provider.get("name")

                if not name:
                    print(f"{colors.RED}Error: neutron.provider_networks.name is missing{colors.RESET}")
                    ok = False
                    continue

                name = name.lower()

                if name in provider_network_names:
                    print(f"{colors.RED}Error: duplicate neutron.provider_networks.name '{name}'{colors.RESET}")
                    ok = False

                provider_network_names.add(name)

            allowed_neutron_networks |= provider_network_names

        service_network_names = set()

        for network in service_networks:
            if not isinstance(network, dict):
                print(f"{colors.RED}Error: invalid service_networks entry '{network}'{colors.RESET}")
                ok = False
                continue

            name = network.get("name")
            neutron_network = network.get("neutron_network")

            if not name:
                print(f"{colors.RED}Error: service_networks.name is missing{colors.RESET}")
                ok = False
                continue

            name = name.lower()

            if name in service_network_names:
                print(f"{colors.RED}Error: duplicate service_networks.name '{name}'{colors.RESET}")
                ok = False

            service_network_names.add(name)

            if not neutron_network:
                print(f"{colors.RED}Error: service_networks.neutron_network is missing{colors.RESET}")
                ok = False
                continue

            neutron_network = neutron_network.lower()

            if neutron_network not in allowed_neutron_networks:
                print(f"{colors.RED}Error: invalid neutron_network '{neutron_network}'. "
                    f"Available neutron networks: {list(allowed_neutron_networks)}{colors.RESET}")
                ok = False

        flavor_id = validate_positive_int(generic_service_flavor_id, "manila.backends.generic.SERVICE_INSTANCE_FLAVOR.ID")
        flavor_ram = validate_positive_int(generic_service_flavor_ram, "manila.backends.generic.SERVICE_INSTANCE_FLAVOR.RAM")
        flavor_vcpus = validate_positive_int(generic_service_flavor_vcpus, "manila.backends.generic.SERVICE_INSTANCE_FLAVOR.VCPUS")
        flavor_disk = validate_positive_int(generic_service_flavor_disk, "manila.backends.generic.SERVICE_INSTANCE_FLAVOR.DISK")

        if None in (flavor_id, flavor_ram, flavor_vcpus, flavor_disk):
            ok = False

    if not isinstance(share_types, list):
        print(f"{colors.RED}Error: manila.share_types must be a list{colors.RESET}")
        ok = False
    else:
        share_type_names = set()

        valid_extra_specs = {
            "driver_handles_share_servers",
            "snapshot_support",
            "create_share_from_snapshot_support",
            "revert_to_snapshot_support",
            "mount_snapshot_support",
        }

        for share_type in share_types:
            if not isinstance(share_type, dict):
                print(f"{colors.RED}Error: invalid share_types entry '{share_type}'{colors.RESET}")
                ok = False,
                continue

            name = share_type.get("name")
            is_public = share_type.get("is_public")
            extra_specs = share_type.get("extra_specs") or []

            if not name:
                print(f"{colors.RED}Error: share_types.name is missing{colors.RESET}")
                ok = False
                continue

            name = name.lower()

            if name in share_type_names:
                print(f"{colors.RED}Error: duplicate share_types.name '{name}'{colors.RESET}")
                ok = False

            share_type_names.add(name)

            if is_public is None:
                print(f"{colors.RED}Error: share_types.{name}.is_public is missing{colors.RESET}")
                ok = False
            elif str(is_public).lower() not in ("yes", "no", "true", "false"):
                print(f"{colors.RED}Error: invalid share_types.{name}.is_public '{is_public}' "
                f"must be yes/no{colors.RESET}")
                ok = False

            if not isinstance(extra_specs, list):
                print(f"{colors.RED}Error: share_types.{name}.extra_specs must be a list{colors.RESET}")
                ok = False
                continue

            is_dhss_enabled = None

            for spec in extra_specs:
                if not isinstance(spec, dict):
                    print(f"{colors.RED}Error: invalid extra_specs entry '{spec}' "
                    f"in share_type '{name}'{colors.RESET}")
                    ok = False
                    continue

                for key, value in spec.items():
                    if key not in valid_extra_specs:
                        print(f"{colors.RED}Error: invalid extra_spec '{key}' "
                            f"in share_type '{name}'. "
                            f"Allowed values: {list(valid_extra_specs)}{colors.RESET}")
                        ok = False

                    if str(value).lower() not in ("yes", "no", "true", "false"):
                        print(f"{colors.RED}Error: invalid value '{value}' "
                        f"for extra_spec '{key}' in share_type '{name}'{colors.RESET}")
                        ok = False

                    if key == "driver_handles_share_servers":
                        is_dhss_enabled = value

            if is_dhss_enabled is None:
                print(f"{colors.RED}Error: extra_specs.driver_handles_share_servers is missing{colors.RESET}")
                ok = False
            else:
                is_dhss_enabled = parse_bool(is_dhss_enabled, False)

                if is_dhss_enabled and enabled_backend == "lvm":
                    print(f"{colors.RED}Error: driver_handles_share_servers=yes "
                        f"is not supported with LVM backend{colors.RESET}")
                    ok = False

                elif enabled_backend == "generic" and not is_dhss_enabled:
                    print(f"{colors.RED}Error: generic backend requires "
                        f"driver_handles_share_servers=yes{colors.RESET}")
                    ok = False

    if not isinstance(shares, list):
        print(f"{colors.RED}Error: manila.shares must be a list{colors.RESET}")
        ok = False
    else:
        share_names = set()

        valid_access_levels = { "rw", "ro" }

        share_type_names = { st.get("name").lower() for st in share_types if isinstance(st, dict) and st.get("name") }

        if enabled_backend == "generic":
            service_network_names = { net.get("name").lower() for net in service_networks if isinstance(net, dict) and net.get("name") }

        lvm_cidr_found = False

        if enabled_backend == "lvm":
            network = ip_network(f"{lvm_share_export_ip}/24", strict=False)

        if enabled_backend == "lvm":
            try:
                export_ip = ip_address(lvm_share_export_ip)
                network = ip_network(f"{export_ip}/24", strict=False)
            except ValueError:
                print(
                    f"{colors.RED}Error: lvm_share_export_ip must be a valid IP address{colors.RESET}"
                )
                ok = False

            for share in shares:
                for rule in share.get("access_rules", []):
                    if rule.get("type") == "ip":
                        try:
                            access_network = ip_network(rule.get("access"), strict=True)

                            if access_network == network:
                                lvm_cidr_found = True
                                break

                        except ValueError:
                            continue

                if lvm_cidr_found:
                    break

            if not lvm_cidr_found:
                print(
                    f"{colors.RED}Error: at least one LVM share must contain "
                    f"the CIDR {network} in access_rules{colors.RESET}"
                )
                ok = False

        for share in shares:
            if not isinstance(share, dict):
                print(f"{colors.RED}Error: invalid shares entry '{share}'{colors.RESET}")
                ok = False
                continue

            name = share.get("name")
            share_type = share.get("share_type")
            share_protocol = share.get("share_protocol")
            share_size = share.get("share_size")
            is_public = share.get("is_public")
            share_network = share.get("share_network")
            access_rules = share.get("access_rules") or []

            if not name:
                print(f"{colors.RED}Error: shares.name is missing{colors.RESET}")
                ok = False
                continue

            name = name.lower()

            if name in share_names:
                print(f"{colors.RED}Error: duplicate shares.name '{name}'{colors.RESET}")
                ok = False

            share_names.add(name)

            if not share_type:
                print(f"{colors.RED}Error: shares.{name}.share_type is missing{colors.RESET}")
                ok = False
            elif share_type.lower() not in share_type_names:
                print(f"{colors.RED}Error: invalid share_type '{share_type}'. "
                f"Available: {list(share_type_names)}{colors.RESET}")
                ok = False

            if not share_protocol:
                print(f"{colors.RED}Error: shares.{name}.share_protocol is missing{colors.RESET}")
                ok = False
            elif share_protocol.upper() not in share_protocols:
                print(f"{colors.RED}Error: invalid share_protocol '{share_protocol}'. "
                    f"Available: {share_protocols}{colors.RESET}")
                ok = False

            size = validate_positive_int(share_size, f"manila.shares.{name}.share_size")

            if size is None: ok = False

            if is_public is None:
                print(f"{colors.RED}Error: shares.{name}.is_public is missing{colors.RESET}")
                ok = False
            elif str(is_public).lower() not in ("yes", "no", "true", "false"):
                print(f"{colors.RED}Error: invalid shares.{name}.is_public "
                f"'{is_public}'{colors.RESET}")
                ok = False

            if not share_network and enabled_backend == "generic":
                print(f"{colors.RED}Error: shares.{name}.share_network is missing{colors.RESET}")
                ok = False

            if share_network and enabled_backend == "lvm":
                print(f"{colors.RED}Shares with the LVM backend do not support the 'share_network' option.{colors.RESET}")
                ok = False

            if not isinstance(access_rules, list):
                print(f"{colors.RED}Error: shares.{name}.access_rules must be a list{colors.RESET}")
                ok = False
                continue

            for idx, rule in enumerate(access_rules):
                if not isinstance(rule, dict):
                    print(f"{colors.RED}Error: shares.{name}.access_rules[{idx}] "
                    f"must be a dictionary{colors.RESET}")
                    ok = False
                    continue

                rule_type = rule.get("type")
                access = rule.get("access")
                level = rule.get("level")

                if not rule_type:
                    print(f"{colors.RED}Error: shares.{name}.access_rules[{idx}].type is missing{colors.RESET}")
                    ok = False

                if not access:
                    print(f"{colors.RED}Error: shares.{name}.access_rules[{idx}].access is missing{colors.RESET}")
                    ok = False

                if rule_type not in ("ip", "user", "cert"):
                    print(f"{colors.RED}Error: invalid access rule type '{rule_type}' in shares.{name}{colors.RESET}")
                    ok = False

                if rule_type == "ip" and not validate_ip(access, field_name=None, error_message=False):
                    try:
                        ip_network(access, strict=True)
                    except ValueError:
                        print(f"{colors.RED}Error: shares.{name}.access_rules[{idx}].access is an invalid CIDR{colors.RESET}")
                        ok = False

                if level not in valid_access_levels:
                    print(f"{colors.RED}Error: invalid access level '{level}' "
                    f"in shares.{name}.access_rules[{idx}] "
                    f"(allowed: rw, ro){colors.RESET}"
                    )
                    ok = False

                protocol = (share_protocol or "").upper()

                if enabled_backend == "lvm" and protocol == "CIFS":
                    allowed_access_types = {"user"}
                else:
                    allowed_access_types = {"ip", "user", "cert"}

                if rule_type not in allowed_access_types:
                    print(f"{colors.RED}Error: invalid access rule type '{rule_type}' "
                        f"for shares.{name}. "
                        f"Allowed values: {allowed_access_types}"
                        f"{colors.RESET}")
                    ok = False
   
    return ok

# --- Compute ---
def validate_compute(config) -> bool:
    ok = True
    warnings = []

    nova_compute_virt_type = get(config, "compute.NOVA_COMPUTE_VIRT_TYPE" or "").lower()

    compute_fields = [
        "compute.NOVA_COMPUTE_VIRT_TYPE",
        "compute.CPU_ALLOCATION_RATIO",
        "compute.RAM_ALLOCATION_RATIO",
        "compute.DISK_ALLOCATION_RATIO",
    ]

    ratios = {
        "compute.CPU_ALLOCATION_RATIO": 1.0,
        "compute.RAM_ALLOCATION_RATIO": 1.0,
        "compute.DISK_ALLOCATION_RATIO": 1.0,
    }

    max_warn_ratios = {
        "compute.CPU_ALLOCATION_RATIO": 16.0,
        "compute.RAM_ALLOCATION_RATIO": 8.0,
        "compute.DISK_ALLOCATION_RATIO": 2.0,
    }

    for field in compute_fields:
        value = get(config, field)
        if value is None:
            print(f"{colors.RED}Error: '{field}' is not set{colors.RESET}")
            ok = False

    if nova_compute_virt_type not in ("kvm", "qemu"):
        print(
            f"{colors.RED}Error: unsupported virt_type '{nova_compute_virt_type}'. "
            f"Allowed values are 'kvm' and 'qemu'.{colors.RESET}"
        )
        ok = False

    for key, min_value in ratios.items():
        value = get(config, key)
        try:
            float_val = float(value)
            if float_val < min_value:
                print(f"{colors.RED}Error: {key} must be >= {min_value}, found: {float_val}{colors.RESET}")
                ok = False
            elif float_val > max_warn_ratios[key]:
                warnings.append(f"{key} is unusually high ({float_val})")
        except (TypeError, ValueError):
            print(f"{colors.RED}Error: {key} must be a decimal number, found: {value}{colors.RESET}")
            ok = False

    for w in warnings:
        print(f"{colors.YELLOW}Warning: {w}{colors.RESET}")

    return ok

# --- Optional services ---
def validate_optional_services(config) -> bool:
    ok = True

    services = [
        "optional_services.INSTALL_CINDER",
        "optional_services.INSTALL_MANILA",
        "optional_services.INSTALL_HORIZON",
    ]

    for field in services:
        value = get(config, field)
        if value not in ("yes", "no", "true", "false"):
            print(f"{colors.RED}Error: '{field}' must be 'yes' or 'no' (got '{value}'){colors.RESET}")
            ok = False
    return ok

# --- OpenStack ---
def validate_openstack(config) -> bool:
    ok = True
    fields = ["openstack.OPENSTACK_RELEASE", "openstack.REGION_NAME"]
    for field in fields:
        value = get(config, field)
        if not value:
            print(f"{colors.RED}Error: '{field}' is not set{colors.RESET}")
            ok = False
    return ok

def validate_all(config) -> bool:
    include_cinder = parse_bool(get(config, "optional_services.INSTALL_CINDER", False))
    include_manila = parse_bool(get(config, "optional_services.INSTALL_MANILA", False))

    ok = True
    ok &= validate_passwords(config)
    ok &= validate_host_network(config)
    ok &= validate_public_network(config)
    ok &= validate_neutron(config)

    if include_cinder:
        ok &= validate_cinder(config)

    if include_manila:
        ok &= validate_manila(config)

    ok &= validate_compute(config)
    ok &= validate_optional_services(config)
    ok &= validate_openstack(config)
    return ok