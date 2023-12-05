#!/usr/bin/env bash
# Configure the Block Storage service (Cinder) (Controller Node)

set -o xtrace
set -e

source openstack.conf

conf_file=/etc/cinder/cinder.conf

install_pkgs(){

apt install cinder-api cinder-scheduler -y

}

conf_cinder(){

crudini --set $conf_file database connection mysql+pymysql://cinder:$DATABASE_PASSWORD@$HOST_IP/cinder

crudini --set $conf_file DEFAULT transport_url rabbit://openstack:$RABBITMQ_PASSWORD@$HOST_IP:5672/
crudini --set $conf_file DEFAULT auth_strategy keystone
crudini --set $conf_file DEFAULT glance_api_servers http://$HOST_IP:9292

crudini --set $conf_file keystone_authtoken www_authenticate_uri http://$HOST_IP:5000/
crudini --set $conf_file keystone_authtoken auth_url http://$HOST_IP:5000/
crudini --set $conf_file keystone_authtoken memcached_servers 127.0.0.1:11211
crudini --set $conf_file keystone_authtoken auth_type password
crudini --set $conf_file keystone_authtoken project_domain_name Default
crudini --set $conf_file keystone_authtoken user_domain_name Default
crudini --set $conf_file keystone_authtoken project_name service
crudini --set $conf_file keystone_authtoken username cinder
crudini --set $conf_file keystone_authtoken password $SERVICE_PASSWORD

crudini --set $conf_file oslo_concurrency lock_path /var/lib/cinder/tmp

su -s /bin/sh -c "cinder-manage db sync" cinder

systemctl restart cinder-scheduler apache2

}

install_pkgs
conf_cinder

echo "Done!"
exit 0
