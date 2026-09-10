# Configure the Shared filesystems service (Manila) (Controller Node)

import os

from ...utils.core.commands import run_command
from ...utils.apt.apt import apt_install, apt_update
from ...utils.config.parser import get
from ...utils.config.setter import set_conf_option
from ...utils.core.system_utils import nc_wait

from ...utils.core.system_utils import is_ubuntu_release

from ..patches.manila.manilaclient import install_manilaclient_in_venv

from ...utils.core import colors

from .. import is_os_release

manila_conf = "/etc/manila/manila.conf"

def install_pkgs(config):

    if not apt_update():
        return False

    manila_packages = ["manila-api", "manila-scheduler", "python3-manilaclient"]

    if is_ubuntu_release("24.04") and is_os_release(config, "gazpacho"):

        manila_packages.remove("python3-manilaclient")

        if not install_manilaclient_in_venv("gazpacho") : return False

        print()

    if not apt_install(manila_packages, "Installing Manila packages..."):
        return False
    
    return True
    
def conf_manila(config):

    print()

    db_password = get(config, "passwords.DATABASE_PASSWORD")
    service_password = get(config, "passwords.SERVICE_PASSWORD")
    rabbitmq_password = get(config, "passwords.RABBITMQ_PASSWORD")
    
    default_share_type_name = get(config, "manila.DEFAULT_SHARE_TYPE_NAME") or "default_share_type"

    os_region_name = get(config, "openstack.REGION_NAME")

    ip_address = get(config, "network.HOST_IP")

    set_conf_option(manila_conf, "database", "connection", f"mysql+pymysql://manila:{db_password}@{ip_address}/manila")

    set_conf_option(manila_conf, "DEFAULT", "transport_url", f"rabbit://openstack:{rabbitmq_password}@{ip_address}")

    set_conf_option(manila_conf, "DEFAULT", "auth_strategy", "keystone")

    set_conf_option(manila_conf, "DEFAULT", "my_ip", ip_address)

    set_conf_option(manila_conf, "DEFAULT", "default_share_type", default_share_type_name)
    set_conf_option(manila_conf, "DEFAULT", "share_name_template", 'share-%s', interpolation=False)
    set_conf_option(manila_conf, "DEFAULT", "rootwrap_config", "/etc/manila/rootwrap.conf")
    set_conf_option(manila_conf, "DEFAULT", "api_paste_config", "/etc/manila/api-paste.ini")

    set_conf_option(manila_conf, "keystone_authtoken", "www_authenticate_uri", f"http://{ip_address}:5000")
    set_conf_option(manila_conf, "keystone_authtoken", "region_name", os_region_name)
    set_conf_option(manila_conf, "keystone_authtoken", "auth_url", f"http://{ip_address}:5000")
    set_conf_option(manila_conf, "keystone_authtoken", "memcached_servers", "127.0.0.1:11211")
    set_conf_option(manila_conf, "keystone_authtoken", "auth_type", "password")
    set_conf_option(manila_conf, "keystone_authtoken", "project_domain_name", "Default")
    set_conf_option(manila_conf, "keystone_authtoken", "user_domain_name", "Default")
    set_conf_option(manila_conf, "keystone_authtoken", "project_name", "service")
    set_conf_option(manila_conf, "keystone_authtoken", "username", "manila")
    set_conf_option(manila_conf, "keystone_authtoken", "password", service_password)

    set_conf_option(manila_conf, "service_user", "send_service_user_token", "True")
    set_conf_option(manila_conf, "service_user", "auth_url", f"http://{ip_address}:5000")

    set_conf_option(manila_conf, "service_user", "auth_type", "password")
    set_conf_option(manila_conf, "service_user", "project_domain_name", "Default")
    set_conf_option(manila_conf, "service_user", "user_domain_name", "Default")
    set_conf_option(manila_conf, "service_user", "project_name", "service")
    set_conf_option(manila_conf, "service_user", "username", "manila")
    set_conf_option(manila_conf, "service_user", "password", service_password)

    set_conf_option(manila_conf, "oslo_concurrency", "lock_path", "/var/lock/manila")

    db_migration_cmd = [
        "sudo", "-u", "manila",
        "manila-manage", "db", "sync"
        ]

    if not run_command(db_migration_cmd, "Running Manila DB Migrations...") : return False

    return True


def finalize(config):

    ip_address = get(config, "network.HOST_IP")

    print()

    if not run_command(["systemctl", "restart", "manila-scheduler", "manila-api"], "Restarting Manila services...", False, None, 3, 5):
        return False
    
    if os.path.exists("/var/lib/manila/manila.sqlite"):
        os.remove("/var/lib/manila/manila.sqlite")

    if not nc_wait(ip_address, 8786) : return False

    return True
    
def run_setup_controller_manila(config, backend_fn, env):

    if not install_pkgs(config): return False 
    if not conf_manila(config): return False
    
    if not finalize(config): return False
    
    if not backend_fn(config, env): return False
    
    print(f"\n{colors.GREEN}Manila configured successfully!{colors.RESET}\n")
    return True