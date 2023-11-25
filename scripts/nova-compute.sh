#!/usr/bin/env bash

set -o xtrace
set -e

source openstack.conf

conf_file=/etc/nova/nova.conf
conf_compute_file=/etc/nova/nova-compute.conf

install_pkgs(){

apt install nova-compute -y

}

conf_nova_compute(){

export OS_USERNAME=admin
export OS_PASSWORD=$ADMIN_PASSWORD
export OS_PROJECT_NAME=admin
export OS_USER_DOMAIN_NAME=Default
export OS_PROJECT_DOMAIN_NAME=Default
export OS_AUTH_URL=http://$HOST_IP:5000/v3
export OS_IDENTITY_API_VERSION=3

crudini --set $conf_compute_file libvirt virt_type $NOVA_COMPUTE_VIRT_TYPE

crudini --set $conf_file scheduler discover_hosts_in_cells_interval 300

systemctl restart nova-api nova-scheduler nova-compute apache2

set +e

openstack compute service list --service nova-compute

su -s /bin/sh -c "nova-manage cell_v2 discover_hosts --verbose" nova

}

create_default_flavors(){

 openstack flavor create m1.tiny --id 1 --ram 512 --disk 1 --vcpus 1 
 openstack flavor create m1.small --id 2 --ram 2048 --disk 20 --vcpus 1 
 openstack flavor create m1.medium --id 3 --ram 4096 --disk 40 --vcpus 2 
 openstack flavor create m1.large --id 4 --ram 8192 --disk 80 --vcpus 4 
 openstack flavor create m1.xlarge --id 5 --ram 16384 --disk 160 --vcpus 8 

}

install_pkgs
conf_nova_compute
create_default_flavors

echo "Done!"
exit 0