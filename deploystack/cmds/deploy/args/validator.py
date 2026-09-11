import ipaddress

def validate_management_args(parser, args):
     if args.os_management_gateway:
        try:
            ipaddress.ip_address(args.os_management_gateway)
        except ValueError:
            parser.error("--os-management-gateway has a invalid Gateway")

def validate_manila_backend(parser, args):
    if args.install_cinder == "no" and args.manila_backend == "generic":
        parser.error("Manila generic backend requires --install-cinder yes")

def validate_manila_args(parser, args):
    if args.install_manila == "no":
        provided = [
            args.manila_lvm_physical_volume is not None,
            args.manila_lvm_image_size_in_gb is not None,
            args.manila_backend is not None,
            args.manila_share_protocols is not None,
        ]

        if any(provided):
            parser.error(
                "Manila options require --install-manila yes"
            )

    validate_manila_backend(parser, args)
    
def validate_cinder_args(parser, args):

    if "nfs" not in args.cinder_enabled_backends:
       
        if args.enable_nfs_snapshots:
            parser.error("--enable-nfs-snapshots require 'nfs' in --cinder-enabled-backends")

    if args.install_cinder == "no":
        provided = [
            args.cinder_physical_volume is not None,
            args.cinder_lvm_image_size_in_gb != 5,
        ]

        if any(provided):
            parser.error("Cinder options require --install-cinder yes")

def validate_cinder_backup_args(parser, args):
    if args.cinder_backup_driver == "no":
        provided = [
            args.cinder_backup_driver,
            args.compression_algorithm,
            args.backup_file_size_in_bytes,
            args.backup_sha_block_size_in_bytes,
            args.backup_workers
        ]

        if any(provided):
            parser.error("Cinder Backup options require --enable-cinder-backup yes")

def validate_deploy_args(parser, args):

    validate_management_args(parser, args)

    validate_manila_args(parser, args)
    validate_cinder_args(parser, args)
    