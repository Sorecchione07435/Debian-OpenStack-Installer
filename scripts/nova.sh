#!/usr/bin/env bash

set -o xtrace
set -e

source openstack.conf

conf=/etc/nova/nova.conf

install_pkgs(){

apt install nova-api nova-conductor nova-novncproxy nova-scheduler -y

}

conf_nova(){

crudini --set $conf api_database connection mysql+pymysql://nova:$DATABASE_PASSWORD@$HOST_IP/nova_api
crudini --set $conf database connection mysql+pymysql://nova:$DATABASE_PASSWORD@$HOST_IP/nova

crudini --set $conf DEFAULT transport_url rabbit://openstack:$RABBITMQ_PASSWORD@$HOST_IP:5672/
crudini --set $conf DEFAULT force_config_drive true

crudini --set $conf api auth_strategy keystone

crudini --set $conf keystone_authtoken www_authenticate_uri http://$HOST_IP:5000/
crudini --set $conf keystone_authtoken auth_url http://$HOST_IP:5000/
crudini --set $conf keystone_authtoken memcached_servers 127.0.0.1:11211
crudini --set $conf keystone_authtoken auth_type password
crudini --set $conf keystone_authtoken project_domain_name Default
crudini --set $conf keystone_authtoken user_domain_name Default
crudini --set $conf keystone_authtoken project_name service
crudini --set $conf keystone_authtoken username nova
crudini --set $conf keystone_authtoken password $SERVICE_PASSWORD

crudini --set $conf vnc enabled true
crudini --set $conf vnc server_listen $HOST_IP
crudini --set $conf vnc server_proxyclient_address $HOST_IP

crudini --set $conf glance api_servers http://$HOST_IP:9292

crudini --set $conf oslo_concurrency lock_path /var/lib/nova/tmp

crudini --set $conf placement region_name RegionOne
crudini --set $conf placement project_domain_name Default
crudini --set $conf placement project_name service
crudini --set $conf placement auth_type password
crudini --set $conf placement user_domain_name Default
crudini --set $conf placement auth_url http://$HOST_IP:5000/v3
crudini --set $conf placement username placement
crudini --set $conf placement password $SERVICE_PASSWORD

su -s /bin/sh -c "nova-manage api_db sync" nova

su -s /bin/sh -c "nova-manage cell_v2 map_cell0" nova
set +e
su -s /bin/sh -c "nova-manage cell_v2 create_cell --name=cell1 --verbose" nova
set -e
su -s /bin/sh -c "nova-manage db sync" nova

su -s /bin/sh -c "nova-manage cell_v2 list_cells" nova

systemctl restart nova-api nova-scheduler nova-conductor nova-novncproxy

}

install_pkgs
conf_nova

echo "Done!"
exit 0
