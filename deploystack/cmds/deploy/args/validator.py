def validate_deploy_args(parser, args):

    # Cinder
    if args.enable_nfs_snapshots == "yes":
        if args.install_cinder != "yes":
            parser.error(
                "--enable-nfs-snapshots requires --install-cinder yes"
            )

        if (
            not args.cinder_enabled_backends
            or "nfs" not in args.cinder_enabled_backends
        ):
            parser.error(
                "--enable-nfs-snapshots requires 'nfs' "
                "in --cinder-enabled-backends"
            )

    # Cinder LVM
    if (
        args.cinder_lvm_image_size_in_gb is not None
        and args.cinder_lvm_image_size_in_gb <= 0
    ):
        parser.error(
            "--cinder-lvm-image-size-in-gb must be greater than 0"
        )

    # Cinder backup
    backup_arguments = [
        args.cinder_backup_driver is not None,
        args.compression_algorithm is not None,
        args.backup_file_size_in_bytes is not None,
        args.backup_sha_block_size_in_bytes is not None,
        args.backup_workers is not None,
    ]

    if args.enable_cinder_backup != "yes" and any(backup_arguments):
        parser.error(
            "Cinder backup options require --enable-cinder-backup yes"
        )

    if (
        args.enable_cinder_backup == "yes"
        and args.install_cinder != "yes"
    ):
        parser.error(
            "--enable-cinder-backup yes requires --install-cinder yes"
        )

    # Manila
    manila_lvm_arguments = [
        args.manila_lvm_physical_volume is not None,
        args.manila_lvm_image_size_in_gb is not None,
        args.manila_volume_group is not None,
    ]

    if args.install_manila != "yes" and any(manila_lvm_arguments):
        parser.error(
            "Manila options require --install-manila yes"
        )

    if (
        args.install_manila == "yes"
        and args.manila_backend == "generic"
        and any(manila_lvm_arguments)
    ):
        parser.error(
            "Manila LVM options cannot be used with "
            "--manila-backend generic"
        )

    if (
        args.manila_lvm_image_size_in_gb is not None
        and args.manila_lvm_image_size_in_gb <= 0
    ):
        parser.error(
            "--manila-lvm-image-size-in-gb must be greater than 0"
        )

    if (
        args.manila_share_protocols is not None
        and args.install_manila != "yes"
    ):
        parser.error(
            "--manila-share-protocols requires --install-manila yes"
        )
