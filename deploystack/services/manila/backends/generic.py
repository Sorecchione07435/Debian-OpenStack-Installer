# Configure the Generic Backend (Share Node)

import os
import json

from ....utils.core.commands import run_command, os_run, os_run_output
from ....utils.apt.apt import apt_install
from ....utils.config.parser import get
from ....utils.config.setter import set_conf_option
from ....utils.config.helpers import parse_bool

from .utils import wait_manila_backend
from .utils.shares import create_shares, create_share_types

from .protocols.nfs import run_setup_nfs

manila_conf = "/etc/manila/manila.conf"

def _set_service_auth(conf, section, username, ip_address, region, password):
    set_conf_option(conf, section, "auth_url", f"http://{ip_address}:5000")
    set_conf_option(conf, section, "auth_type", "password")
    set_conf_option(conf, section, "memcached_servers", "127.0.0.1:11211")
    set_conf_option(conf, section, "project_domain_name", "Default")
    set_conf_option(conf, section, "user_domain_name", "Default")
    set_conf_option(conf, section, "region_name", region)
    set_conf_option(conf, section, "project_name", "service")
    set_conf_option(conf, section, "username", username)
    set_conf_option(conf, section, "password", password)

def install_pkgs():

    print()

    if not apt_install(["manila-share"], "Installing Manila Share package..."): return False

    return True 

def conf_generic_backend(config):

    protocols = get(config, "manila.SHARE_PROTOCOLS", default=["NFS"])
    ip_address = get(config, "network.HOST_IP")

    backend_name = get(config, "manila.backends.generic.BACKEND_NAME")
    service_password = get(config, "passwords.SERVICE_PASSWORD")
    os_region_name = get(config, "openstack.REGION_NAME")

    generic_service_instance_flavor_id = get(config, "manila.backends.generic.SERVICE_INSTANCE_FLAVOR.ID")
    generic_interface_driver = get(config, "manila.backends.generic.INTERFACE_DRIVER")
    generic_service_image_name = get(config, "manila.backends.generic.SERVICE_IMAGE_NAME")

    generic_share_server_to_tenant_network = parse_bool(get(config, "manila.backends.generic.CONNECT_SHARE_SERVER_TO_TENANT_NETWORK", False))

    enabled_share_protocols = ",".join(protocols)

    share_helpers = get(config, "manila.SHARE_HELPERS") or []
    
    helpers = []

    if "NFS" in protocols:
        if not run_setup_nfs(): return False

    for helper in share_helpers:
            for helper_type, config in helper.items():
                helper_name = config.get("name")
                helpers.append(f"{helper_type}={helper_name}")

    helpers = [f"{helper_type}={config.get('name')}" for helper in share_helpers for helper_type, config in helper.items()]

    set_conf_option(manila_conf, "DEFAULT", "share_helpers", ",".join(helpers))

    set_conf_option(manila_conf, "DEFAULT", "enabled_share_backends", "generic")
    set_conf_option(manila_conf, "DEFAULT", "enabled_share_protocols", enabled_share_protocols)

    _set_service_auth(manila_conf, "neutron", "neutron", ip_address, os_region_name, service_password)
    _set_service_auth(manila_conf, "nova", "nova", ip_address, os_region_name, service_password)
    _set_service_auth(manila_conf, "glance", "glance", ip_address, os_region_name, service_password)
    _set_service_auth(manila_conf, "cinder", "cinder", ip_address, os_region_name, service_password)

    set_conf_option(manila_conf, "generic", "share_backend_name", backend_name)
    set_conf_option(manila_conf, "generic", "share_driver", "manila.share.drivers.generic.GenericShareDriver")
    set_conf_option(manila_conf, "generic", "driver_handles_share_servers", "True")
    set_conf_option(manila_conf, "generic", "connect_share_server_to_tenant_network", str(generic_share_server_to_tenant_network))
    set_conf_option(manila_conf, "generic", "service_instance_flavor_id", str(generic_service_instance_flavor_id))
    set_conf_option(manila_conf, "generic", "service_image_name", generic_service_image_name)
    set_conf_option(manila_conf, "generic", "service_instance_user", "manila")
    set_conf_option(manila_conf, "generic", "service_instance_password", "manila")
    set_conf_option(manila_conf, "generic", "interface_driver", generic_interface_driver)
    set_conf_option(manila_conf, "generic", "connect_security_service_method", "ssh")
    set_conf_option(manila_conf, "generic", "service_instance_launch_timeout", "300")

    return True

def finalize(env):

    print()

    if not run_command(["systemctl", "restart", "manila-api", "manila-scheduler", "manila-share"], "Restarting Manila Share services...", False, None, 3, 5):
        return False

    print()

    if not wait_manila_backend(env=env):
        return False

    return True

def finalize_generic_backend(config, env):

    create_shares_enabled = parse_bool(get(config, "manila.CREATE_SHARES") , False)

    manila_temp_image_file = "/tmp/manila-service-image.qcow2"
    manila_image_url = "https://tarballs.opendev.org/openstack/manila-image-elements/images/manila-service-image-1.3.0-77-g8cd2097.qcow2"

    generic_service_image_name = get(config, "manila.backends.generic.SERVICE_IMAGE_NAME")

    default_type_shares = get(config, "manila.share_types") or []

    generic_service_instance_flavor_name = get(config, "manila.backends.generic.SERVICE_INSTANCE_FLAVOR.NAME")
    generic_service_instance_flavor_id = get(config, "manila.backends.generic.SERVICE_INSTANCE_FLAVOR.ID")
    generic_service_instance_flavor_ram = get(config, "manila.backends.generic.SERVICE_INSTANCE_FLAVOR.RAM")
    generic_service_instance_flavor_vcpus = get(config, "manila.backends.generic.SERVICE_INSTANCE_FLAVOR.VCPUS")
    generic_service_instance_flavor_disk = get(config, "manila.backends.generic.SERVICE_INSTANCE_FLAVOR.DISK")

    service_networks = get(config, "manila.backends.generic.service_networks") or []

    networks_list = json.loads(os_run_output(["openstack", "network", "list", "-f", "json"], env=env) or "[]")
    images_list = json.loads(os_run_output(["openstack", "image", "list", "-f", "json"], env=env) or "[]")
    flavors_list = json.loads(os_run_output(["openstack", "flavor", "list", "-f", "json"], env=env) or "[]")
    
    if not create_share_types(default_type_shares=default_type_shares, env=env): return False

    manila_service_image_exists = any(image.get("Name") == generic_service_image_name for image in images_list)

    if not manila_service_image_exists:
        print()

        if not os.path.exists(manila_temp_image_file):
            if not run_command(["wget", "--continue", "--progress=dot:giga", "--tries=3", "--timeout=30", "--read-timeout=60","-O", manila_temp_image_file, manila_image_url], "Downloading Manila service image... (this may take a while) ", timeout=3600): return False

        if os.path.exists(manila_temp_image_file):
            if not os_run(["openstack", "image", "create", generic_service_image_name, "--file", manila_temp_image_file, "--disk-format", "qcow2", "--container-format", "bare", "--public"], "Uploading Manila image to Glance...", env=env): return False
        
        try:
            os.remove(manila_temp_image_file)
        except FileNotFoundError:
            pass

    manila_service_flavor_exists = any(flavor.get("Name") == generic_service_instance_flavor_name for flavor in flavors_list)

    if not manila_service_flavor_exists:
        print()
        if not os_run(["openstack", "flavor", "create", "--id", str(generic_service_instance_flavor_id), "--ram", str(generic_service_instance_flavor_ram), "--disk", str(generic_service_instance_flavor_disk), "--vcpus", str(generic_service_instance_flavor_vcpus), generic_service_instance_flavor_name], "Creating Manila service flavor...", env=env): return False

    share_networks_list = json.loads(os_run_output(["openstack", "share", "network", "list", "-f", "json"], env=env) or "[]")

    line_printed = False

    for service_net in service_networks:

        network_name = service_net["name"]
        neutron_network = service_net["neutron_network"]

        neutron_network_id = ""
        neutron_subnet_id = ""
    
        for network in networks_list:
            if network["Name"] == neutron_network:
                neutron_network_id = network.get("ID") or network.get("id")

        neutron_network_subnets_list = json.loads(os_run_output(["openstack", "subnet", "list", "--network", neutron_network_id, "-f", "json"], env=env) or "[]")

        for subnet in neutron_network_subnets_list:
            neutron_subnet_id = subnet.get("ID") or subnet.get("id")
            break

        tenant_share_network_exists = any(sn.get("Name") == network_name or sn.get("name") == network_name for sn in share_networks_list)

        if not tenant_share_network_exists:
            if not line_printed:
                line_printed = True
                print()

            if not os_run(["openstack", "share", "network", "create", "--name", network_name, "--neutron-net-id", str(neutron_network_id), "--neutron-subnet-id", str(neutron_subnet_id)], f"Creating tenant share '{network_name}' network...", env=env): return False

    if create_shares_enabled:
        shares = get(config, "manila.shares") or []

        if not create_shares(shares=shares, env=env, dhss=True): return False

    return True

def run_setup_generic_backend(config, env):

    if not install_pkgs(): return False

    conf_generic_backend(config)

    if not finalize(env): return False

    if not finalize_generic_backend(config, env): return False

    return True