import ipaddress
import psutil
import subprocess
import re
import os

from .parser import get
from ..core import colors
from ..core.system_utils import get_device_for_path

from pathlib import Path

prohibited_pw_chars = [' ', '$', '`', '\\']
VALID_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")


ALLOWED_NFS_OPTIONS = {
    "vers",
    "proto",
    "port",
    "timeo",
    "retrans",
    "rsize",
    "wsize",
    "hard",
    "soft",
    "ro",
    "rw",
    "sync",
    "async",
    "bg",
    "fg",
}

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

def is_valid_nfs_options(options: str) -> bool:
    try:
        options = options.removeprefix("-o").strip()

        for option in options.split(","):
            option = option.strip()

            if not option:
                continue

            key = option.split("=", 1)[0]

            if key not in ALLOWED_NFS_OPTIONS:
                return False

        return True

    except (AttributeError, ValueError):
        return False

def is_valid_nfs_share(share: str) -> bool:
    pattern = r"^[^:/\s]+:/[^:\s]+$"
    return bool(re.match(pattern, share))

def validate_port(port):
    try:
        port = int(port)

        if not 1 <= port <= 65535:
            return None

        return port

    except (TypeError, ValueError):
        return None

def is_valid_path(path: str, field: str) -> bool:
    try:
        if not isinstance(path, str):
            raise ValueError

        path = path.strip()

        if not path:
            raise ValueError

        if not path.startswith("/"):
            raise ValueError

        if not VALID_PATH_RE.fullmatch(path):
            raise ValueError

        parts = path.split("/")

        parts = parts[1:]

        if any(part in ("", ".", "..") for part in parts):
            raise ValueError

        Path(path)

        return True

    except (TypeError, ValueError):
        print(
            f"{colors.RED}Error: '{field}' is an invalid path"
            f"{colors.RESET}"
        )
        return False

def get_root_device():
    try:
        return subprocess.check_output(
            ["findmnt", "-n", "-o", "SOURCE", "/"]
        ).decode().strip()
    except subprocess.CalledProcessError:
        return None

def validate_positive_int(value, field):
    try:
        value = int(value)
        if value <= 0:
            print(f"{colors.RED}Error: {field} must be greater than zero{colors.RESET}")
            return None
        return value
    except ValueError:
        print(f"{colors.RED}Error: {field} must be an integer{colors.RESET}")
        return None

def get_root_disk():
    device = get_root_device()
    if not device:
        return None

    try:
        return subprocess.check_output(
            ["lsblk", "-no", "PKNAME", device]
        ).decode().strip()
    except subprocess.CalledProcessError:
        return None

def is_system_disk(device):
    root_disk = get_root_disk()
    if not root_disk:
        return False

    device = device.replace("/dev/", "").split("p")[0]

    return device == root_disk

def is_safe_lvm_device(device):
    if not device:
        return False

    if device.startswith("/dev/loop"):
        return True

    if is_system_disk(device):
        return False

    unsafe_prefixes = ["/dev/sda", "/dev/nvme0n1", "/dev/vda"]

    if any(device.startswith(p) for p in unsafe_prefixes):
        return False

    return True

def parse_bool(value, default=False):
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("yes", "true", "1")

def get_provider_networks(config):

    networks_list = get(config, "neutron.provider_networks", [])
    result = []

    for net in networks_list:
        net_info = {
            "bridge": net.get("bridge"),
            "name": net.get("name"),
            "type": net.get("type")
        }
        result.append(net_info)

    return result

def interface_exists(if_name: str) -> bool:
    return if_name in psutil.net_if_addrs()

def is_loop_busy(loop_dev) -> bool:
    result = subprocess.run(
        ["losetup", loop_dev],
        capture_output=True,
        text=True
    )
    return result.returncode == 0

def validate_ip(value: str, field_name: str, error_message = True) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        if error_message:
            print(f"{colors.RED}Error: '{field_name}' contains an invalid IP: {value}{colors.RESET}")
        return False

def validate_cidr(value: str, field_name: str) -> bool:
    try:
        ipaddress.ip_network(value, strict=False)
        return True
    except ValueError:
        print(f"{colors.RED}Error: '{field_name}' contains an invalid network CIDR: {value}{colors.RESET}")
        return False

def is_loop_device(path: str) -> bool:
    try:
        result = subprocess.run(
            ["lsblk", "-no", "TYPE", path],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip() == "loop"
    except Exception:
        return False
    
def validate_ip_in_network(ip, network_cidr, label, colors):
    try:
        net = ipaddress.ip_network(network_cidr, strict=False)
        if ipaddress.ip_address(ip) not in net:
            print(f"{colors.RED}Error: {label} '{ip}' is not within network '{network_cidr}'{colors.RESET}")
            return False
        return True
    except ValueError:
        return False 