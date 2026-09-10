import subprocess
import os
import logging
import time
import json

from dataclasses import dataclass, field

from ..config.parser import get_conf_option

from ..core.commands import run_command_output

from ..core.system_utils import service_exists, is_debian

from ...services import validate_os_release_available

from ..core import colors

MARKER_FILE = "/var/lib/openstack_installer/deploy_complete"

# Name of the environment variable that enables debugging.
# Set DEPLOYSTACK_DEBUG=1 (or "true"/"yes") to turn it on.
# In production it should be left unset (or =0), so the logger stays at WARNING level.
DEBUG_ENV_VAR = "DEPLOYSTACK_DEPLOYMENT_CHECK_ENABLE_DEBUG"

logger = logging.getLogger(__name__)


def _env_flag_enabled(var_name: str) -> bool:
    """Return True if the given environment variable represents a 'true' value."""
    value = os.environ.get(var_name, "")
    return value.strip().lower() in ("1", "true", "yes", "on")


def is_debug_enabled() -> bool:
    """Determine whether debugging is active for this module."""
    return _env_flag_enabled(DEBUG_ENV_VAR)


def configure_logging(debug: bool | None = None) -> None:
    """
    Configure the module's logger.

    - debug=None (default): reads the state from DEPLOYSTACK_DEBUG.
    - debug=True: forces DEBUG level (verbose, development/troubleshooting only).
    - debug=False: forces WARNING level (production behavior, quiet).
    """
    if debug is None:
        debug = is_debug_enabled()

    level = logging.DEBUG if debug else logging.WARNING
    logger.setLevel(level)

    # Avoid adding duplicate handlers if this function is called more than once
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        logger.addHandler(handler)

    logger.propagate = False

    if debug:
        logger.debug(f"Debug logging enabled for {__name__} (via {DEBUG_ENV_VAR})")


# Initial logger configuration at module import time,
# based on the current state of the environment variable.
configure_logging()

cinder_pkgs = ["cinder-api", "cinder-scheduler", "cinder-volume", "tgt"]
manila_pkgs = ["manila-api", "manila-scheduler", "python3-manilaclient", "manila-share"]

class CheckCategory:
    SERVICES = "Services"
    PACKAGES = "Packages"
    CONFIG = "Config files"
    ENDPOINTS = "Endpoints"

@dataclass
class CheckResult:
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.failed) == 0

    def __str__(self):
        lines = [f"{colors.GREEN}PASSED:{colors.RESET} {s}" for s in self.passed] + [f"{colors.RED}FAILED:{colors.RESET} {s}" for s in self.failed]
        return "\n".join(lines)

def check_keystone_auth() -> tuple[bool, str]:
    try:
        logger.debug("Requesting Keystone token...")
        token = run_command_output(["openstack", "token", "issue", "-f", "value", "-c", "id"])

        if token.strip():
            logger.debug("Keystone token obtained successfully")
            return True, ""

        logger.debug("Empty Keystone token")
        return False, "Empty Keystone token received"
    except subprocess.CalledProcessError as e:
        logger.debug(f"Keystone auth failed: {e}")
        return False, "Keystone authentication failed. Check credentials or OS_* environment variables"

    except subprocess.TimeoutExpired:
        logger.debug("Timeout while requesting Keystone")
        return False, "Keystone request timed out. Check Keystone service availability"

    except FileNotFoundError:
        logger.debug("'openstack' command not found")
        return False, "OpenStack client command not found"
    
    except Exception as e:
        logger.exception("Unexpected Keystone authentication error")
        return False, str(e)

def is_package_installed(pkg_name: str) -> bool:
    try:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", pkg_name],
            capture_output=True, text=True, check=True
        )
        installed = "install ok installed" in result.stdout
        logger.debug(f"Package '{pkg_name}': {'installed' if installed else 'NOT installed'}")
        return installed
    except subprocess.CalledProcessError:
        logger.debug(f"Package '{pkg_name}': dpkg-query failed (probably not installed)")
        return False
    
def check_endpoint(service_name: str) -> bool:
    try:
        output = run_command_output(["openstack", "endpoint", "list", "--service", service_name, "-f", "json", "-c Enabled"])

        result = bool(json.loads(output))
        logger.debug(f"Endpoint '{service_name}': {'present' if result else 'absent'}")
        return result

    except (
        json.JSONDecodeError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError
    ) as e:
        logger.debug(f"Endpoint '{service_name}': error during check ({e})")
        return False

def check_service_active(svc: str) -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", svc],
            timeout=5
        )
        active = result.returncode == 0
        logger.debug(f"Service '{svc}': {'active' if active else 'NOT active'}")
        return active
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.debug(f"Service '{svc}': error during check ({e})")
        return False

def check_deployment(include_endpoints: bool = True):
    logger.debug(f"check_deployment(include_endpoints={include_endpoints}) started")
    result = CheckResult()

    services_list = ["apache2", "glance-api"]

    if service_exists("nova-api.service") and is_package_installed("nova-api"):
        services_list.append("nova-api.service")

    if service_exists("neutron-server.service"):
        services_list.append("neutron-server")
    elif service_exists("neutron-api.service"):
        services_list.append("neutron-api")
    elif service_exists("neutron-periodic-workers.service"):
        services_list.append("neutron-periodic-workers")

    checks = [
        (CheckCategory.SERVICES, services_list, check_service_active),
        (CheckCategory.PACKAGES, ["apache2", "nova-common", "glance-api", "neutron-server"], is_package_installed),
        (CheckCategory.CONFIG, [
            "/etc/keystone/keystone.conf", "/etc/glance/glance-api.conf",
            "/etc/nova/nova.conf", "/etc/neutron/neutron.conf"
        ], os.path.isfile),
    ]

    def add_check(category, items, fn):
        checks.append((category, items, fn))

    def add_packages_check(items):
        add_check(CheckCategory.PACKAGES, items, is_package_installed)

    def add_services_check(items):
        add_check(CheckCategory.SERVICES, items, check_service_active)

    def add_config_files_check(items):
        add_check(CheckCategory.CONFIG, items, os.path.isfile)

    def add_endpoints_check(items):
        add_check(CheckCategory.ENDPOINTS, items, check_endpoint)

    if all(is_package_installed(pkg) for pkg in cinder_pkgs):
        logger.debug("Cinder detected: adding related checks")
        add_services_check(["cinder-scheduler", "cinder-volume", "tgt"])
        add_packages_check(cinder_pkgs)
        add_config_files_check(["/etc/cinder/cinder.conf", "/etc/tgt/conf.d/cinder.conf"])

        if include_endpoints:
            add_endpoints_check(["volumev3"])

    if all(is_package_installed(pkg) for pkg in manila_pkgs):
        logger.debug("Manila detected: adding related checks")
        manila_conf = "/etc/manila/manila.conf"

        add_config_files_check([manila_conf])
        add_services_check(["manila-api", "manila-scheduler", "manila-share"])

        if include_endpoints:
            add_endpoints_check(["sharev2"])

            if not is_debian() and not validate_os_release_available("gazpacho"):
                add_endpoints_check(["share"])

        manila_backend = (get_conf_option(manila_conf, "DEFAULT", "enabled_share_backends") or "").lower()
        manila_protocols_list = (get_conf_option(manila_conf, "DEFAULT", "enabled_share_protocols") or "").lower()

        manila_protocols = [protocol for protocol in manila_protocols_list.split(",") if protocol]

        logger.debug(f"Manila backend='{manila_backend}' protocols={manila_protocols}")

        if manila_backend == "lvm":
            add_packages_check(["lvm2", "nfs-kernel-server"])

            if "cifs" in manila_protocols:
                smb_conf = "/etc/samba/smb.conf"

                samba_services = ["smbd.service"]

                disable_netbios = get_conf_option(smb_conf, "global", "disable netbios")
                
                if service_exists("nmbd.service") and disable_netbios == "no":
                    samba_services.append("nmbd.service")

                add_packages_check(["samba", "samba-common-bin"])
                add_config_files_check([smb_conf])
                add_services_check(samba_services)

        if "nfs" in manila_protocols:

            add_packages_check(["nfs-kernel-server"])

            if service_exists("nfs-server.service"):
                add_services_check(["nfs-server.service"])

    if include_endpoints:
        add_endpoints_check(["identity", "compute", "image", "network"])

    for category, items, check_fn in checks:
        for item in items:
            label = f"[{category}] {item}"
            if check_fn(item):
                result.passed.append(label)
            else:
                result.failed.append(label)

    logger.debug(f"check_deployment completed: {len(result.passed)} passed, {len(result.failed)} failed")
    return result

def check_env_variables():
    required_vars = [
        "OS_PROJECT_DOMAIN_NAME",
        "OS_USER_DOMAIN_NAME",
        "OS_PROJECT_NAME",
        "OS_USERNAME",
        "OS_PASSWORD",
        "OS_AUTH_URL",
        "OS_IDENTITY_API_VERSION",
        "OS_IMAGE_API_VERSION"
    ]

    missing = []
    empty = []

    for var in required_vars:
        value = os.environ.get(var)
        if value is None:
            missing.append(var)
        elif value.strip() == "":
            empty.append(var)

    if missing or empty:
        error_msg = []

        if missing:
            error_msg.append(f"Missing vars: {', '.join(missing)}")
        if empty:
            error_msg.append(f"Empty vars: {', '.join(empty)}")

        raise RuntimeError(" | ".join(error_msg))

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="DeployStack deployment checks")
    parser.add_argument(
        "--debug",
        action="store_true",
        help=f"Enable debug logging (equivalent to setting {DEBUG_ENV_VAR}=1). "
             "Do not use in production."
    )
    args = parser.parse_args()

    if args.debug:
        configure_logging(debug=True)

    outcome = check_deployment(include_endpoints=False)
    print(outcome)

    if not outcome.ok:
        exit(1)

    try:
        check_env_variables()
    except RuntimeError as e:
        logger.error(f"Environment variables error: {e}")
        exit(1)

    endpoint_result = check_deployment(include_endpoints=True)
    print(endpoint_result)

    exit(0 if endpoint_result.ok else 1)

def check_cinder_available() -> bool:

    if not all(is_package_installed(pkg) for pkg in cinder_pkgs): return False

    if not check_endpoint("volumev3"): return False

    if not all(check_service_active(service) for service in ["cinder-scheduler", "cinder-volume", "tgt"]) : return False
   
    return True

def is_cinder_available() -> bool:

    if not check_cinder_available():
        print(f"{colors.RED}Cinder service is not installed or not available.{colors.RESET}\n")
        print(f"{colors.YELLOW}  • If you want block storage support, run 'deploystack deploy --allinone' or include Cinder in your deployment{colors.RESET}")
        print(f"{colors.YELLOW}  • Alternatively, continue without Cinder, but volume-based features will not be available{colors.RESET}\n")
        return False

    return True   

def is_openstack_ready() -> bool:

    if not os.path.exists(MARKER_FILE):
        return False

    debug = is_debug_enabled()

    try:
        check_env_variables()
    except RuntimeError:
        print(f"{colors.YELLOW}Shell is not authenticated. Source the environment file first:{colors.RESET}\n")
        print(f"  {colors.YELLOW}source /root/admin-openrc.sh{colors.RESET}  or")
        print(f"  {colors.GREEN}source /root/demo-openrc.sh{colors.RESET}\n")
        return False
 
    auth_ok, auth_error = check_keystone_auth()

    if not auth_ok:
        print(f"{colors.RED}OpenStack authentication failed.{colors.RESET}\n")
        print(f"{colors.YELLOW}  • {auth_error}{colors.RESET}")
        print(f"{colors.YELLOW}  • Verify your OpenStack credentials and environment variables.{colors.RESET}")
        print(f"{colors.YELLOW}  • Source an admin environment file first:{colors.RESET}")
        print(f"    {colors.GREEN}source /root/admin-openrc.sh{colors.RESET}\n")
        return False

    base_check = check_deployment(include_endpoints=False)
    if not debug:
        if not base_check.ok:
            print(f"{colors.RED}OpenStack is not deployed yet.{colors.RESET}\n")
            print(f"{colors.YELLOW}  • Run 'deploy --allinone' for a full automated deployment{colors.RESET}")
            print(f"{colors.YELLOW}  • Or run 'deploy --config-file <config_file>' with a custom config{colors.RESET}\n")
            return False

    endpoint_check = check_deployment(include_endpoints=True)
    if not endpoint_check.ok:
        if debug:
            print()

        print(f"{colors.RED}OpenStack is deployed but services are not fully operational:{colors.RESET}\n")
        print(f"{endpoint_check}\n")
        return False

    return True

def mark_deployment_complete():
    os.makedirs(os.path.dirname(MARKER_FILE), exist_ok=True)
    with open(MARKER_FILE, "w") as f:
        f.write(f"Deployment completed at {time.ctime()}\n")