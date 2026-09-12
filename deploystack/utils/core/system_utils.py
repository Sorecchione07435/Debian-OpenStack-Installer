
import random
import string
import socket
import json

import platform
import subprocess
import sys
import os

from time import sleep, time

from ...utils.core import colors
from ...utils.config.parser import get

VIRTUAL_FILESYSTEMS = {
    "devtmpfs", "tmpfs", "proc", "sysfs", "overlay", "squashfs",
    "cgroup", "cgroup2", "devpts", "mqueue", "debugfs", "tracefs",
    "securityfs", "pstore", "bpf", "autofs", "hugetlbfs",
}

def get_parent_disk(device):
    dev_name = device.removeprefix("/dev/")
    try:
        result = subprocess.run(
            ["lsblk", "-no", "PKNAME", f"/dev/{dev_name}"],
            capture_output=True,
            text=True,
            check=True,
        )

        lines = {l.strip() for l in result.stdout.splitlines() if l.strip()}
        parent = lines[0] if lines else ""
        if parent:
            return f"/dev/{parent}"
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return device

def resolve_device_to_disk(device):
    if not device or not device.startswith("/dev"):
        return None

    seen = set()

    current = device
    while current not in seen:
        seen.add(current)
        parent = get_parent_disk(current)
        if parent == current:
            break
        current = parent

    return parent

def get_device_for_path(path):
    if not path:
        return None

    try:
        result = subprocess.run(
            ["findmnt", "-n", "-o", "SOURCE", "-T", path],
            capture_output=True,
            text=True,
            check=True,
        )

        return result.stdout.strip()

    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    
def get_physical_disk(path):

    device = get_device_for_path(path)
    if not device:
        return None

    if device in VIRTUAL_FILESYSTEMS or not device.startswith("/dev/"):
        return None

    return resolve_device_to_disk(device)

def get_vg_physical_disks(vg_name):

    try:
        subprocess.run(["vgscan"], check=True)
    except subprocess.CalledProcessError:
        pass

    try:
        subprocess.run(["vgscan", "--cache"], check=True)
    except subprocess.CalledProcessError:
        pass

    try:
        result = subprocess.run(
            ["pvs", "--noheadings", "-o", "pv_name", "--select", f"vg_name={vg_name}"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    pv_devices = [line.strip() for line in result.stdout.splitlines() if line.strip()]

    disks = set()
    for pv in pv_devices:
        disk = resolve_device_to_disk(pv)
        if disk:
            disks.add(disk)

    return disks

def is_package_installed(package_name: str | list[str]) -> bool:
    try:
        if isinstance(package_name, list):
            return all(
                is_package_installed(pkg)
                for pkg in package_name
            )

        result = subprocess.run(
            ["dpkg", "-s", package_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        return result.returncode == 0

    except FileNotFoundError:
        return False
    
def is_ubuntu_release(target_version: str) -> bool:
    info = platform.freedesktop_os_release()

    return (
        info.get("ID") == "ubuntu"
        and info.get("VERSION_ID") == target_version
    )

def is_debian():
    try:
        with open("/etc/os-release") as f:
            data = f.read().lower()

        for line in data.splitlines():
            if line.startswith("id="):

                id_value = line.split("=")[1].strip().strip('"')
                return id_value == "debian"
        return False
    except FileNotFoundError:
        return False

def iface_exists(iface: str) -> bool:
    result = subprocess.run(["ip", "link", "show", iface],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    return result.returncode == 0

def nc_wait(addr: str, port: int, timeout: int = 30) -> bool:

    start_time = time()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    while True:
        if sock.connect_ex((addr, port)) == 0:
            sock.close()
            return True
        elif time() - start_time > timeout:
            print(f"\n{colors.RED}ERROR:{colors.RESET} Service at {addr}:{port} did not respond within {timeout} seconds.")
            sock.close()

            sys.exit(1);
            return False
        sleep(1)

def is_module_loaded(module_name):
    with open("/proc/modules") as f:
        return any(
            line.split()[0] == module_name
            for line in f
        )

def service_exists(service_name):
    result = subprocess.run(["systemctl", "list-unit-files", service_name], capture_output=True, text=True)
    return service_name in result.stdout

def check_ifupdown():
    result = subprocess.run(
        ["dpkg", "-s", "ifupdown"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    return result.returncode == 0

def has_hw_virtualization():
    try:
        with open("/proc/cpuinfo") as f:
            cpuinfo = f.read()

        cpu_support = ("vmx" in cpuinfo) or ("svm" in cpuinfo)

        kvm_available = False
        try:
            open("/dev/kvm").close()
            kvm_available = True
        except:
            pass

        return cpu_support and kvm_available

    except:
        return False

def get_free_loops(count=1):
    result = subprocess.run(
        ["losetup", "-J"],
        capture_output=True,
        text=True,
        check=True
    )
    
    used = set()
    for dev in json.loads(result.stdout).get("loopdevices", []):
        used.add(dev["name"])

    loops = []
    i = 0
    while len(loops) < count:
        candidate = f"/dev/loop{i}"
        if candidate not in used:
            loops.append(candidate)
        i += 1

    return loops

def generate_password(length=12):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def build_openstack_env(config):
    env = os.environ.copy()

    ip_address = get(config, "network.HOST_IP")
    admin_password = get(config, "passwords.ADMIN_PASSWORD")

    env.update({
        "OS_USERNAME": "admin",
        "OS_PASSWORD": admin_password,
        "OS_PROJECT_NAME": "admin",
        "OS_USER_DOMAIN_NAME": "Default",
        "OS_PROJECT_DOMAIN_NAME": "Default",
        "OS_AUTH_URL": f"http://{ip_address}:5000/v3",
        "OS_IDENTITY_API_VERSION": "3",
    })

    return env
