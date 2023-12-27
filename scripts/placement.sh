#!/usr/bin/env bash
# Configure the Placement service (Placement)

set -o xtrace
set -e

source openstack.conf
conf_file=/etc/placement/placement.conf

install_pkgs(){

apt install placement-api -y

}

conf_placement()
{

crudini --set $conf_file placement_database connection mysql+pymysql://placement:$DATABASE_PASSWORD@$HOST_IP/placement

crudini --set $conf_file api auth_strategy keystone

crudini --set $conf_file keystone_authtoken auth_url http://$HOST_IP:5000/v3
crudini --set $conf_file keystone_authtoken memcached_servers 127.0.0.1:11211
crudini --set $conf_file keystone_authtoken auth_type password
crudini --set $conf_file keystone_authtoken project_domain_name Default
crudini --set $conf_file keystone_authtoken user_domain_name Default
crudini --set $conf_file keystone_authtoken project_name service
crudini --set $conf_file keystone_authtoken username placement
crudini --set $conf_file keystone_authtoken password $SERVICE_PASSWORD

su -s /bin/sh -c "placement-manage db sync" placement

systemctl restart apache2

}

install_pkgs
conf_placement

echo "Done!"
exit 0
