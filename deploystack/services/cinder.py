# Configure the Block Storage service (Cinder) (Controller + Storage Node)

import pwd
import grp
import os
import subprocess
import json

from pathlib import Path

from ..utils.core.commands import run_command, run_command_sync, run_command_output
from ..utils.apt.apt import apt_install
from ..utils.config.parser import get
from ..utils.config.setter import set_conf_option
from ..utils.core.system_utils import nc_wait
from ..utils.core import colors
from ..utils.core.system_utils import service_exists, is_debian, is_package_installed, get_device_for_path, get_physical_disk, get_vg_physical_disks
from ..utils.lvm.loopback import write_loopback_lvm_env, setup_loopback_service
from ..utils.lvm import get_vg_for_pv, ensure_system_user_with_run_command

from ..utils.config.helpers import parse_bool, get_nfs_share_for_path, parse_nfs_source

cinder_conf = "/etc/cinder/cinder.conf"
tgt_conf_path = "/etc/tgt/conf.d/cinder.conf"
lvm_conf_path = "/etc/lvm/lvm.conf"

def install_pkgs(config):

    install_cinder_backup = parse_bool(get(config, "cinder.ENABLE_CINDER_BACKUP", False))
    enabled_backends = get(config, "cinder.ENABLED_BACKENDS", []) or []
    
    packages = ["cinder-scheduler", "cinder-api", "cinder-volume"]

    if "lvm" in enabled_backends:
        packages.append("tgt")

    if "nfs" in enabled_backends:
        use_external_share = parse_bool(get(config, "cinder.backends.nfs.USE_EXTERNAL_SHARE"), False)

        packages.append("nfs-common")

        if not use_external_share:
            packages.append("nfs-kernel-server")

    if install_cinder_backup:
        packages.append("cinder-backup")

    if not apt_install(packages, ux_text=f"Installing Cinder packages...") : return False
    
    return True

def conf_lvm_backend(config):

    lvm_physical_volume = get(config, "cinder.backends.lvm.PHYSICAL_VOLUME")
    lvm_image_file_path = get(config, "cinder.backends.lvm.CINDER_VOLUME_LVM_IMAGE_FILE_PATH")
    lvm_loop_dev = get(config, "cinder.backends.lvm.CINDER_VOLUME_LVM_PHYSICAL_PV_LOOP_PATH")
    lvm_image_size_in_gb = get(config, "cinder.backends.lvm.CINDER_VOLUME_LVM_IMAGE_SIZE_IN_GB")

    VG_NAME = get(config, "cinder.backends.lvm.VOLUME_GROUP")

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

            if not ensure_system_user_with_run_command("cinder"):
                return False

            uid = pwd.getpwnam("cinder").pw_uid
            gid = grp.getgrnam("cinder").gr_gid

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
            ["vgcreate", VG_NAME, lvm_dev],
            f"Creating volume group {VG_NAME}..."
        ):
            return False

    elif vg == VG_NAME:
        pass

    else:
        print(
            f"{colors.RED}"
            f"{lvm_dev} already belongs to VG '{vg}', expected '{VG_NAME}'"
            f"{colors.RESET}"
        )
        return False
    
    os.makedirs(os.path.dirname(tgt_conf_path), exist_ok=True)

    if not os.path.exists(tgt_conf_path):
        with open(tgt_conf_path, "w") as f:
            f.write("include /var/lib/cinder/volumes/*")

    return True

def conf_nfs_backend(config):

    print()

    use_external_share = parse_bool(get(config, "cinder.backends.nfs.USE_EXTERNAL_SHARE"), False)
    nfs_share = get(config, "cinder.backends.nfs.NFS_SHARE")
    mount_point_base = get(config, "cinder.backends.nfs.MOUNT_POINT_BASE") or "/var/lib/cinder/mnt"

    if not use_external_share:
        ip_address = get(config, "network.HOST_IP")

        exports_file = Path("/etc/exports")
        nfs_mount_point = Path(mount_point_base)

        if not run_command(["systemctl", "enable", "--now", "nfs-kernel-server"], "Enabling NFS Kernel Server...") : return False

        try:
            nfs_mount_point.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(f"{colors.RED}ERROR: Unable to create NFS mount point {nfs_mount_point}: {e}{colors.RESET}")
            return False

        try:
            uid = pwd.getpwnam("cinder").pw_uid
            gid = grp.getgrnam("cinder").gr_gid

            os.chown(nfs_mount_point, uid, gid)

        except (KeyError, OSError) as e:
            print(
                f"{colors.RED}"
                f"ERROR: Unable to set ownership on {nfs_mount_point}: {e}"
                f"{colors.RESET}"
            )
            return False
        
        try:
            _, export_path = nfs_share.split(":", 1)
        except ValueError:
            return False

        export_line = (f"{export_path} {ip_address}(rw,sync,no_subtree_check,no_root_squash)")
        
        lines = set()

        if exports_file.exists():
            with exports_file.open("r") as f:
                lines = {line.strip() for line in f}

        if export_line not in lines:
            with exports_file.open("a") as f:
                f.write(f"{export_line}\n")

        print()

        os.makedirs(export_path, exist_ok=True)

        if not run_command(["exportfs", "-ra"], "Applying NFS exports...") : return False

    cinder_nfs_shares = Path("/etc/cinder/nfs_shares")

    try:
        cinder_nfs_shares.parent.mkdir(parents=True, exist_ok=True)

        lines = set()

        if cinder_nfs_shares.exists():
            with cinder_nfs_shares.open("r") as f:
                lines = {line.strip() for line in f if line.strip()}

        if nfs_share not in lines:
            with cinder_nfs_shares.open("a") as f:
                f.write(f"{nfs_share}\n")

    except OSError as e:
        print(
            f"{colors.RED}"
            f"ERROR: Unable to configure "
            f"{cinder_nfs_shares}: {e}"
            f"{colors.RESET}"
        )
        return False

    return True

def conf_cinder_backup(config):

    backup_driver = get(config, "cinder.backup.DRIVER").lower()

    backup_compression_algorithm = get(config, "cinder.backup.COMPRESSION_ALGORITHM").lower()

    backup_workers = get(config, "cinder.backup.BACKUP_WORKERS")

    backup_file_size = get(config, "cinder.backup.BACKUP_FILE_SIZE")
    backup_sha_block_size_bytes = get(config, "cinder.backup.BACKUP_SHA_BLOCK_SIZE_BYTES")
    
    enabled_cinder_backends = get(config, "cinder.ENABLED_BACKENDS", []) or []

    if backup_driver == "posix":

        backup_filesystem_path = get(config, "cinder.backup.drivers.posix.BACKUP_PATH")

        os.makedirs(backup_filesystem_path, exist_ok=True)

        uid = pwd.getpwnam("cinder").pw_uid
        gid = grp.getgrnam("cinder").gr_gid
        os.chown(backup_filesystem_path, uid, gid)

        os.chmod(backup_filesystem_path, 0o750)

        run_command_sync(["sudo", "-u", "cinder", "touch", os.path.join(backup_filesystem_path, "test")])

        os.remove(os.path.join(backup_filesystem_path, "test"))

        set_conf_option(cinder_conf, "DEFAULT", "backup_driver", "cinder.backup.drivers.posix.PosixBackupDriver")
        set_conf_option(cinder_conf, "DEFAULT", "backup_posix_path", backup_filesystem_path)

        if "lvm" in enabled_cinder_backends:
            vg_name = get(config, "cinder.backends.lvm.VOLUME_GROUP")

            backup_disk = get_physical_disk(backup_filesystem_path)
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

            backup_source = get_nfs_share_for_path(backup_filesystem_path)
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

        nfs_share = get(config, "cinder.backup.drivers.nfs.NFS_SHARE")
        mount_point_base_dir = get(config, "cinder.backup.drivers.nfs.MOUNT_POINT_BASE")

        mount_options = get(config, "cinder.backup.drivers.nfs.MOUNT_OPTIONS") or None

        use_external_share = parse_bool(get(config, "cinder.backup.drivers.nfs.USE_EXTERNAL_SHARE"), False)

        ip_address = get(config, "network.HOST_IP")

        if not use_external_share:

            if not is_package_installed("nfs-kernel-server"):
                print()

                if not apt_install(["nfs-kernel-server"], "Installing NFS Kernel Server package...") : return False

            exports_file = Path("/etc/exports")

            try:
                _, export_path = nfs_share.split(":", 1)
            except ValueError:
                return False

            os.makedirs(export_path, exist_ok=True)
            
            export_line = (f"{export_path} {ip_address}(rw,sync,no_subtree_check,no_root_squash)")

            lines = set()

            if exports_file.exists():
                with exports_file.open("r") as f:
                    lines = {line.strip() for line in f}

            if export_line not in lines:
                with exports_file.open("a") as f:
                    f.write(f"{export_line}\n")

            print()

            if not run_command(["exportfs", "-ra"], "Applying NFS exports...") : return False

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

        set_conf_option(cinder_conf, "DEFAULT", "backup_driver", "cinder.backup.drivers.nfs.NFSBackupDriver")

        set_conf_option(cinder_conf, "DEFAULT", "backup_mount_point_base", mount_point_base_dir)
        set_conf_option(cinder_conf, "DEFAULT", "backup_share", nfs_share)

        if mount_options:
            set_conf_option(cinder_conf, "DEFAULT", "backup_mount_options", mount_options)

    set_conf_option(cinder_conf, "DEFAULT", "backup_compression_algorithm", backup_compression_algorithm)
        
    set_conf_option(cinder_conf, "DEFAULT", "backup_workers", str(backup_workers))

    set_conf_option(cinder_conf, "DEFAULT", "backup_file_size", str(backup_file_size))
    set_conf_option(cinder_conf, "DEFAULT", "backup_sha_block_size_bytes", str(backup_sha_block_size_bytes))

    return True

def conf_cinder(config):

    print()
     
    db_password = get(config, "passwords.DATABASE_PASSWORD")
    rabbitmq_password = get(config, "passwords.RABBITMQ_PASSWORD")
    os_region_name = get(config, "openstack.REGION_NAME")

    service_password = get(config, "passwords.SERVICE_PASSWORD")

    ip_address = get(config, "network.HOST_IP")

    default_volume_type = get(config, "cinder.DEFAULT_VOLUME_TYPE")

    enabled_backends = get(config, "cinder.ENABLED_BACKENDS", []) or []

    set_conf_option(cinder_conf, "DEFAULT", "transport_url", f"rabbit://openstack:{rabbitmq_password}@{ip_address}:5672/")
    set_conf_option(cinder_conf, "DEFAULT", "glance_api_servers", f"http://{ip_address}:9292")
    set_conf_option(cinder_conf, "DEFAULT", "enabled_backends", ",".join(str(x) for x in enabled_backends))
    set_conf_option(cinder_conf, "DEFAULT", "default_volume_type", default_volume_type)

    set_conf_option(cinder_conf, "DEFAULT", "my_ip", ip_address)

    set_conf_option(cinder_conf, "keystone_authtoken", "memcached_servers", "127.0.0.1:11211")
    set_conf_option(cinder_conf, "keystone_authtoken", "www_authenticate_uri", f"http://{ip_address}:5000/")
    set_conf_option(cinder_conf, "keystone_authtoken", "region_name", os_region_name)
    set_conf_option(cinder_conf, "keystone_authtoken", "auth_url", f"http://{ip_address}:5000/")
    set_conf_option(cinder_conf, "keystone_authtoken", "auth_type", "password")
    set_conf_option(cinder_conf, "keystone_authtoken", "project_domain_name", "Default")
    set_conf_option(cinder_conf, "keystone_authtoken", "user_domain_name", "Default")
    set_conf_option(cinder_conf, "keystone_authtoken", "project_name", "service")
    set_conf_option(cinder_conf, "keystone_authtoken", "username", "cinder")
    set_conf_option(cinder_conf, "keystone_authtoken", "password", service_password)

    if "lvm" in enabled_backends:

        lvm_backend_name = get(config, "cinder.backends.lvm.BACKEND_NAME")
    
        volume_clear = get(config, "cinder.backends.lvm.VOLUME_CLEAR")
        volume_clear_size = int(get(config, "cinder.backends.lvm.VOLUME_CLEAR_SIZE"))

        target_scsi_ip_address = get(config, "cinder.backends.lvm.TARGET_IP_ADDRESS") or ip_address
  
        VG_NAME = get(config, "cinder.backends.lvm.VOLUME_GROUP")

        if isinstance(target_scsi_ip_address, dict) or target_scsi_ip_address is None or "{network.HOST_IP}" in str(target_scsi_ip_address):
                target_scsi_ip_address = ip_address 
        
        target_scsi_ip_address = str(target_scsi_ip_address)
        
        set_conf_option(cinder_conf, "lvm", "volume_driver", "cinder.volume.drivers.lvm.LVMVolumeDriver")
        set_conf_option(cinder_conf, "lvm", "volume_group", VG_NAME)
        set_conf_option(cinder_conf, "lvm", "volume_backend_name", lvm_backend_name)
        set_conf_option(cinder_conf, "lvm", "iscsi_protocol", "iscsi")
        set_conf_option(cinder_conf, "lvm", "iscsi_helper", "tgtadm")
        set_conf_option(cinder_conf, "lvm", "volume_clear", volume_clear)
        set_conf_option(cinder_conf, "lvm", "volume_clear_size", str(volume_clear_size))
        set_conf_option(cinder_conf, "lvm", "target_ip_address", target_scsi_ip_address)

    if "nfs" in enabled_backends:
        nfs_backend_name = get(config, "cinder.backends.nfs.BACKEND_NAME")
        nfs_mount_point_base = get(config, "cinder.backends.nfs.MOUNT_POINT_BASE") or "/var/lib/cinder/mnt"

        nfs_mount_options = get(config, "cinder.backends.nfs.MOUNT_OPTIONS") or None

        sparsed_volumes = get(config, "cinder.backends.nfs.NFS_SPARSED_VOLUMES", "sparse")
        sparsed_volumes = str(sparsed_volumes).lower() == "sparse"

        nfs_used_ratio = get(config, "cinder.backends.nfs.NFS_USED_RATIO", 0.95) 
        nfs_oversub_ratio = get(config, "cinder.backends.nfs.NFS_OVERSUB_RATIO", 1.0) 

        enable_snapshots = parse_bool(get(config, "cinder.backends.nfs.ENABLE_SNAPSHOTS"), False)

        set_conf_option(cinder_conf, "nfs", "volume_driver", "cinder.volume.drivers.nfs.NfsDriver")
        set_conf_option(cinder_conf, "nfs", "volume_backend_name", nfs_backend_name)
        set_conf_option(cinder_conf, "nfs", "nfs_shares_config", "/etc/cinder/nfs_shares")
        set_conf_option(cinder_conf, "nfs", "nfs_mount_point_base", nfs_mount_point_base)

        if nfs_mount_options:
            set_conf_option(cinder_conf, "nfs", "nfs_mount_options", nfs_mount_options)

        set_conf_option(cinder_conf, "nfs", "nfs_sparsed_volumes", str(sparsed_volumes))

        set_conf_option(cinder_conf, "nfs", "nfs_used_ratio", str(nfs_used_ratio))
        set_conf_option(cinder_conf, "nfs", "nfs_oversub_ratio", str(nfs_oversub_ratio))

        if enable_snapshots:
            set_conf_option(cinder_conf, "nfs", "nfs_snapshot_support", "True")

    set_conf_option(cinder_conf, "service_user", "project_domain_name", "Default")
    set_conf_option(cinder_conf, "service_user", "project_name", "service")
    set_conf_option(cinder_conf, "service_user", "user_domain_name", "Default")
    set_conf_option(cinder_conf, "service_user", "password", service_password)
    set_conf_option(cinder_conf, "service_user", "username", "cinder")
    set_conf_option(cinder_conf, "service_user", "auth_url", f"http://{ip_address}:5000/v3")
    set_conf_option(cinder_conf, "service_user", "auth_type", "password")
    set_conf_option(cinder_conf, "service_user", "send_service_user_token", "True")

    set_conf_option(cinder_conf, "glance", "memcached_servers", "127.0.0.1:11211")
    set_conf_option(cinder_conf, "glance", "region_name", os_region_name)
    set_conf_option(cinder_conf, "glance", "project_domain_name", "Default")
    set_conf_option(cinder_conf, "glance", "project_name", "service")
    set_conf_option(cinder_conf, "glance", "www_authenticate_uri", f"http://{ip_address}:5000/v3")
    set_conf_option(cinder_conf, "glance", "user_domain_name", "Default")
    set_conf_option(cinder_conf, "glance", "password", service_password)
    set_conf_option(cinder_conf, "glance", "username", "glance")
    set_conf_option(cinder_conf, "glance", "auth_url", f"http://{ip_address}:5000/v3")
    set_conf_option(cinder_conf, "glance", "auth_type", "password")

    set_conf_option(cinder_conf, "nova", "region_name", os_region_name)
    set_conf_option(cinder_conf, "nova", "project_domain_name", "Default")
    set_conf_option(cinder_conf, "nova", "project_name", "service")
    set_conf_option(cinder_conf, "nova", "user_domain_name", "Default")
    set_conf_option(cinder_conf, "nova", "password", service_password)
    set_conf_option(cinder_conf, "nova", "username", "nova")
    set_conf_option(cinder_conf, "nova", "auth_url", f"http://{ip_address}:5000/v3")
    set_conf_option(cinder_conf, "nova", "auth_type", "password")

    set_conf_option(cinder_conf, "database", "connection", f"mysql+pymysql://cinder:{db_password}@{ip_address}/cinder")

    set_conf_option(cinder_conf, "oslo_concurrency", "lock_path", "/var/lib/cinder/tmp")

    set_conf_option(cinder_conf, "os_brick", "lock_path", "/var/lib/cinder/os-brick")

    db_migration_cmd = [
    "sudo", "-u", "cinder",
    "cinder-manage", "db", "sync"
    ]

    if not run_command(db_migration_cmd, "Running Cinder DB Migrations...") : return False
    
    return True

def finalize(config):

    ip_address = get(config, "network.HOST_IP")
    install_cinder_backup = parse_bool(get(config, "cinder.ENABLE_CINDER_BACKUP", False))

    enabled_backends = get(config, "cinder.ENABLED_BACKENDS", []) or []

    print()

    cinder_services = [
        "cinder-scheduler",
        "cinder-volume", 
        "apache2", 
    ]

    if "lvm" in enabled_backends:
        cinder_services.append("tgt")

    if install_cinder_backup:
        cinder_services.append("cinder-backup")

    if service_exists("cinder-api.service") and is_debian():
        cinder_services.append("cinder-api")

    if not run_command(["systemctl", "restart"] + cinder_services, "Restarting Cinder services...", False, None, 3, 5): return False
    
    if not nc_wait(ip_address, 8776) : return False

    return True

def create_volume_types(config, env):

    enabled_backends = get(config, "cinder.ENABLED_BACKENDS", []) or []

    volumes_types = json.loads(run_command_output(["openstack", "volume", "type", "list", "-f", "json"], env=env))

    def configure_volume_type(backend):

        backend_name = get(config, f"cinder.backends.{backend}.BACKEND_NAME")
        volume_type_name = get(config, f"cinder.backends.{backend}.VOLUME_TYPE_NAME")

        volume_type = next((item for item in volumes_types if item.get("Name") == volume_type_name), None)

        if volume_type is None:
            print()

            if not run_command(["openstack", "volume", "type", "create", volume_type_name], f"Creating volume type '{volume_type_name}'...", env=env) : return False

            needs_backend_config = True
        else:
            volume_type = json.loads(run_command_output(["openstack", "volume", "type", "show", volume_type_name, "-f", "json"], env=env))
            needs_backend_config = (volume_type.get("properties", {}).get("volume_backend_name") != backend_name)

        if needs_backend_config:
            if not run_command(["openstack", "volume", "type", "set", volume_type_name, "--property", f"volume_backend_name={backend_name}"], f"Configuring backend '{backend_name}' for volume type '{volume_type_name}'...", env=env) : return False

        return True

    for backend in enabled_backends:
        if backend in ("lvm", "nfs"):
            if not configure_volume_type(backend):
                return False

    return True

def run_setup_cinder(config, env):

    install_cinder_backup = parse_bool(get(config, "cinder.ENABLE_CINDER_BACKUP", False))

    enabled_backends = get(config, "cinder.ENABLED_BACKENDS", []) or []

    if not install_pkgs(config): return False 

    if "lvm" in enabled_backends:
        if not conf_lvm_backend(config): return False

        using_loopback = not get(config, "cinder.backends.lvm.PHYSICAL_VOLUME")
        
        if using_loopback:
            if not write_loopback_lvm_env("cinder", description="Cinder Loopback LVM", before_services="cinder-volume.service tgt.service"): return False   
            if not setup_loopback_service("cinder"): return False   

    if "nfs" in enabled_backends:
        if not conf_nfs_backend(config) : return False

    if install_cinder_backup:
        if not conf_cinder_backup(config) : return False

    if not conf_cinder(config): return False    

    if not create_volume_types(config, env) : return False

    if not finalize(config): return False
    
    print(f"\n{colors.GREEN}Cinder configured successfully!{colors.RESET}\n")
    return True
    