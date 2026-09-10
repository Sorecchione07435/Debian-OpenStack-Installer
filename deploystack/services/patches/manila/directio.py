import os

from pathlib import Path 

from ....utils.core.commands import run_command_sync, run_command
from ....utils.core import colors

from ....utils.config.setter import set_conf_option

manila_lvm_file_path = "/usr/lib/python3/dist-packages/manila/share/drivers/lvm.py"
manila_share_service_drop_in_conf_file = "/etc/systemd/system/manila-share.service.d/deploystack-directio.conf"

def create_manila_patch_script(manila_lvm_file_path):

    script_path = Path(
        "/usr/local/bin/manila-directio-patcher"
    )

    script = f"""#!/usr/bin/python3

from pathlib import Path

path = Path("{manila_lvm_file_path}")

if not path.exists():
    raise SystemExit(0)

code = path.read_text()

if "use_direct_io=use_direct_io" in code:
    print("Patching Manila Share LVM for direct_io")

    code = code.replace(
        "use_direct_io=use_direct_io",
        "use_direct_io=False",
    )

    path.write_text(code)
"""

    script_path.write_text(script)
    script_path.chmod(0o755)

def create_manila_patch_service():

    service_path = Path("/etc/systemd/system/manila-directio-patcher.service")

    service = """[Unit]
Description=DeployStack Manila Direct IO LVM Patcher

[Service]
Type=oneshot
ExecStart=/usr/local/bin/manila-directio-patcher
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""

    service_path.write_text(service)

def configure_manila_directio_service():

    if not os.path.exists(manila_share_service_drop_in_conf_file):
        Path(manila_share_service_drop_in_conf_file).parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        Path(manila_share_service_drop_in_conf_file).touch()

    set_conf_option(
        manila_share_service_drop_in_conf_file,
        "Unit",
        "Requires",
        "manila-directio-patcher.service",
    )

    set_conf_option(
        manila_share_service_drop_in_conf_file,
        "Unit",
        "After",
        "manila-directio-patcher.service",
    )

def patch_manila_directio():
    with open(manila_lvm_file_path, "r") as f:
        code = f.read()

    if "use_direct_io=use_direct_io" in code:
        print(
            f"{colors.YELLOW}"
            "Ubuntu Resolute detected, a patch will be applied to "
            "Manila Share to force direct_io to be disabled for "
            "snapshots to work properly."
            f"{colors.RESET}"
        )

        new_code = code.replace(
            "use_direct_io=use_direct_io",
            "use_direct_io=False",
        )

        with open(manila_lvm_file_path, "w") as f:
            f.write(new_code)

    return True

def run_setup_directio_patch():

    create_manila_patch_script(manila_lvm_file_path)
    create_manila_patch_service()

    configure_manila_directio_service()

    if not patch_manila_directio():
        return False

    print()

    if not run_command(
        ["systemctl", "daemon-reload"],
        "Reloading systemd daemon..."
    ):
        return False

    if not run_command(
        ["systemctl", "enable", "manila-directio-patcher.service"],
        "Enabling Manila DirectIO patcher service..."
    ):
        return False

    print()

    if not run_command(
        ["systemctl", "restart", "manila-share"],
        "Restarting Manila Share...",  False, None, 3, 5
    ):
        return False

    return True
