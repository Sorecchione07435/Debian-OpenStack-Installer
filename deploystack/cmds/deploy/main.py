import ipaddress
import sys
import os

from ...utils.core import colors

from ...utils.core.system_utils import is_ubuntu_release

from .generator import generate_config_file, config_openstack
from .runner import deploy as runner_deploy

from .args.validator import validate_deploy_args

def init_parser(subparsers):
     
    parser = subparsers.add_parser(
    "deploy",
    help="Start the OpenStack Deployment on the current node"
)

    deployment_options = parser.add_argument_group("Deployment Options")
    general_options = parser.add_argument_group("General Options")

    deployment_group = deployment_options.add_mutually_exclusive_group(required=True)

    gateway = general_options.add_mutually_exclusive_group(required=False)

    manila = parser.add_argument_group("Manila Options")
    cinder = parser.add_argument_group("Cinder Options")

    cinder_backup = parser.add_argument_group("Cinder Backup Options")

    deployment_group.add_argument(
        "-aio",
        "--allinone",
        action="store_true",
        help="Runs a complete OpenStack deployment using an automatically generated configuration."
    )

    deployment_group.add_argument(
        "-c",
        "--config-file",
        help="Path to the configuration file"
    )

    deployment_options.add_argument(
        "--generate-only",
        action="store_true",
        help="Generates a pre-compiled configuration file for the current system without starting the deployment"
    )

    cinder.add_argument(
        "--install-cinder",
        type=str,
        choices=["yes", "no"],
        default="yes",
        help="Choosing whether to install Cinder (Block Storage) service (yes/no)"
    )

    general_options.add_argument(
        "--install-horizon",
        type=str,
        choices=["yes", "no"],
        default="yes",
        help="Choosing whether to install Horizon (Dashboard) service (yes/no)"
    )

    manila.add_argument(
        "--install-manila",
        type=str,
        choices=["yes", "no"],
        default="no",
        help="Choosing whether to install Manila (Shared Filesystems) service (yes/no)"
    )

    cinder.add_argument(
        "--cinder-enabled-backends",
        nargs="+",
        choices=["lvm", "nfs"],
        dest="cinder_enabled_backends",
        help="One or more Cinder volume backends to enable (choices: lvm, nfs)."
    )

    cinder.add_argument(
        "--enable-nfs-snapshots",
        choices=["yes", "no"],
        type=str,
        help="Enable or disable snapshot support for the Cinder NFS driver. (yes/no)"
    )

    cinder.add_argument(
        "--cinder-lvm-image-size-in-gb",
        type=int,
        default=None,
        help="Size of the Cinder LVM image in GB (default: 5)"
    )

    cinder.add_argument(
        "--cinder-physical-volume",
        type=str,
        help="The physical volume to use for Cinder (example: /dev/sdb)"
    )

    cinder.add_argument(
        "--cinder-volume-group",
        type=str,
        default="cinder-volumes",
        help="Name of the LVM volume group. (default: cinder-volumes)"
    )

    cinder_backup.add_argument(
        "--enable-cinder-backup",
        type=str,
        choices=["yes", "no"],
        default="no",
        help="Choose whether to enable the Cinder Backup service (yes/no)"
    )

    cinder_backup.add_argument(
        "--cinder-backup-driver",
        type=str,
        choices=["posix", "nfs"],
        default=None,
        help="Cinder backup driver to use (posix, nfs)"
    )

    cinder_backup.add_argument(
        "--compression-algorithm",
        type=str,
        choices=["zlib", "bz2", "zstd", "none"],
        #default="zlib",
        default=None,
        help="Backup compression algorithm"
    )

    cinder_backup.add_argument(
        "--backup-file-size-in-bytes",
        type=int,
        #default=1999994880,
        default=None,
        help="Maximum size of each backup file in bytes (Default: 1999994880)"
    )

    cinder_backup.add_argument(
        "--backup-sha-block-size-in-bytes",
        type=int,
        #default=32768,
        default=None,
        help="Block size in bytes used for SHA checksum calculation (Default: 32768)"
    )

    cinder_backup.add_argument(
        "--backup-workers",
        type=int,
        #default=1,
        default=None,
        help="Number of concurrent backup operations"
    )

    manila_lvm_storage = manila.add_mutually_exclusive_group()

    manila_lvm_storage.add_argument(
        "--manila-lvm-physical-volume",
        type=str,
        help="The physical volume to use for Manila LVM (example: /dev/sdc)"
    )
    
    manila_lvm_storage.add_argument(
        "--manila-lvm-image-size-in-gb",
        type=int,
        help="Size of the Manila LVM image in GB (default: 5)"
    )

    manila_lvm_storage.add_argument(
        "--manila-volume-group",
        type=str,
        help="Name of the LVM volume group used for Manila shares"
    )

    general_options.add_argument(
        "--neutron-driver",
        type=str,
        choices=["ovs", "ovn"],
        default="ovs",
        dest="neutron_driver",
        help="The Neutron Driver that will be used to configure networks in OpenStack"
    )

    manila.add_argument(
        "--manila-backend",
        type=str,
        choices=["generic", "lvm"],
        dest="manila_backend",
        help="The Manila Backend that will be used to configure shares in OpenStack"
    )

    manila.add_argument(
        "--manila-share-protocols",
        nargs="+",
        choices=["nfs", "cifs"],
        dest="manila_share_protocols",
        help="One or more Manila share protocols (choices: nfs, cifs)."
    )

    general_options.add_argument(
        "--os-release",
        type=str,
        dest="os_release",
        help="The OpenStack release to install for deployment (default: caracal)"
    )

    general_options.add_argument(
        "--os-management-interface",
        type=str,
        help="Override the OpenStack management network interface used by services (example: eth0, ens18)"
    )

    gateway.add_argument(
        "--os-management-gateway",
        type=str,
        default=None,
        help="Override the OpenStack management gateway interface used by services"
    )

    gateway.add_argument(
        "--default-gateway",
        type=str,
        default=None,
        help="Default network gateway used by the OpenStack host for outbound traffic."
    )

    parser.set_defaults(cmd_parser=parser)

    return parser

def deploy(parser, args) -> None:

    parser = args.cmd_parser

    if args.allinone:

        validate_deploy_args(parser, args)

        config_file_path = generate_config_file()

        cinder_flag = args.install_cinder
        horizon_flag = args.install_horizon
        manila_flag = args.install_manila

        neutron_driver = args.neutron_driver or "ovs"

        enable_cinder_backup = args.enable_cinder_backup

        os_release: str

        if neutron_driver not in ("ovs", "ovn"):
            neutron_driver = "ovs"

        manila_backend = args.manila_backend or "lvm"

        if manila_backend not in ("generic", "lvm"):
            manila_backend = "lvm"

        manila_share_protocols = (
            args.manila_share_protocols
            or ["nfs"]
        )

        manila_lvm_physical_volume = (
            args.manila_lvm_physical_volume
            if manila_flag == "yes" and args.manila_lvm_physical_volume
            else ""
        )

        cinder_enabled_backends = None
        cinder_lvm_volume_group = None

        cinder_nfs_snapshots_enabled = None

        cinder_backup_driver = None
        cinder_backup_compression_algorithm = None
        cinder_backup_file_size_in_bytes = None
        cinder_backup_sha_block_size_in_bytes = None
        cinder_backup_workers = None

        if cinder_flag == "yes":

            cinder_enabled_backends = (
                args.cinder_enabled_backends
                if args.cinder_enabled_backends is not None
                else ["lvm"]
            )

            cinder_lvm_volume_group = (
                args.cinder_volume_group
                if args.cinder_volume_group is not None
                else "cinder-volumes"
            )

            cinder_lvm_size = (
                args.cinder_lvm_image_size_in_gb
                if args.cinder_lvm_image_size_in_gb is not None
                else 5
                )
 
            cinder_physical_volume = (
                args.cinder_physical_volume
                if cinder_flag == "yes"
                else ""
            )

            cinder_nfs_snapshots_enabled = (
                args.enable_nfs_snapshots
                if cinder_flag == "yes"
                else "yes"
            )

        if enable_cinder_backup == "yes":
            cinder_backup_driver = (
                args.cinder_backup_driver
                if args.cinder_backup_driver is not None
                else "posix"
            )

            cinder_backup_compression_algorithm = (
                args.compression_algorithm
                if args.compression_algorithm is not None
                else "zlib"
            )

            cinder_backup_file_size_in_bytes = (
                args.backup_file_size_in_bytes
                if args.backup_file_size_in_bytes is not None
                else 1999994880
            )

            cinder_backup_sha_block_size_in_bytes = (
                args.backup_sha_block_size_in_bytes
                if args.backup_sha_block_size_in_bytes is not None
                else 32768
            )

            cinder_backup_workers = (
                args.backup_workers
                if args.backup_workers is not None
                else 1
            )
        
        manila_lvm_vg = (
            args.manila_volume_group
            if manila_flag == "yes" and args.manila_volume_group is not None
            else "manila-shares" if manila_flag == "yes"
            else None
        )

        manila_lvm_size = (
            args.manila_lvm_image_size_in_gb
            if args.manila_lvm_image_size_in_gb is not None
            else 5
        ) if manila_flag == "yes" else 0

        if args.os_release is not None:
            os_release = args.os_release
        elif is_ubuntu_release("20.04"):
            os_release = "ussuri"
        elif is_ubuntu_release("22.04"):
            os_release = "yoga"
        elif is_ubuntu_release("24.04"):
            os_release = "caracal"
        elif is_ubuntu_release("26.04"):
            os_release = "gazpacho"

        config_openstack(
            config_file_path=config_file_path,

            install_horizon=horizon_flag,
            install_cinder=cinder_flag,
            install_manila=manila_flag,

            cinder_enabled_backends=cinder_enabled_backends,
            cinder_nfs_snapshots_enabled=cinder_nfs_snapshots_enabled,

            cinder_lvm_vg=cinder_lvm_volume_group,
            cinder_physical_volume=cinder_physical_volume,
            cinder_lvm_image_size_in_gb=cinder_lvm_size,

            enable_cinder_backup=enable_cinder_backup,
            cinder_backup_driver=cinder_backup_driver,
            compression_algorithm=cinder_backup_compression_algorithm,
            backup_file_size_in_bytes=cinder_backup_file_size_in_bytes,
            backup_sha_block_size_in_bytes=cinder_backup_sha_block_size_in_bytes,
            backup_workers=cinder_backup_workers,

            manila_lvm_vg=manila_lvm_vg,

            manila_lvm_physical_volume=manila_lvm_physical_volume,
            manila_lvm_image_size_in_gb=manila_lvm_size,

            neutron_driver=neutron_driver,

            manila_backend=manila_backend,
            manila_share_protocols=manila_share_protocols,

            os_mgmt_iface=args.os_management_interface,

            default_gateway=args.default_gateway,
            os_mgmt_gateway=args.os_management_gateway,

            os_release=os_release,
        )

        if args.generate_only:
            print(
                f"{colors.GREEN}Configuration file generated in "
                f"'{config_file_path}{colors.RESET}'\n"
            )
            print(
                f"You can start the deployment later with "
                f"'deploystack deploy --config-file {config_file_path}'"
            )
            sys.exit(0)

        runner_deploy(config_file_path)

    else:

        if args.config_file is None or not os.path.exists(args.config_file):
            print(
                f"{colors.RED}Configuration file not found. "
                f"Generate it first using "
                f"'deploystack generate-config <file>'{colors.RESET}"
            )
            sys.exit(1)

        runner_deploy(args.config_file)