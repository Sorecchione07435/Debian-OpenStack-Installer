#!/usr/bin/env bash

set -o xtrace
set -e

source openstack.conf

conf_file=/etc/glance/glance-api.conf

install_pkgs(){

apt install glance -y

}

conf_glance(){

crudini --set $conf_file database connection mysql+pymysql://glance:$DATABASE_PASSWORD@$HOST_IP/glance

crudini --set $conf_file keystone_authtoken www_authenticate_uri http://$HOST_IP:5000
crudini --set $conf_file keystone_authtoken auth_url http://$HOST_IP:5000
crudini --set $conf_file keystone_authtoken memcached_servers 127.0.0.1:11211
crudini --set $conf_file keystone_authtoken auth_type password
crudini --set $conf_file keystone_authtoken project_domain_name Default
crudini --set $conf_file keystone_authtoken user_domain_name Default
crudini --set $conf_file keystone_authtoken project_name service
crudini --set $conf_file keystone_authtoken username glance
crudini --set $conf_file keystone_authtoken password $SERVICE_PASSWORD

crudini --set $conf_file paste_deploy flavor keystone

crudini --set $conf_file glance_store stores file,http
crudini --set $conf_file glance_store default_store file
crudini --set $conf_file glance_store filesystem_store_datadir /var/lib/glance/images/

su -s /bin/sh -c "glance-manage db_sync" glance

systemctl restart glance-api

}

upload_cirros_image(){

wget http://download.cirros-cloud.net/0.4.0/cirros-0.4.0-x86_64-disk.img

export OS_USERNAME=admin
export OS_PASSWORD=$ADMIN_PASSWORD
export OS_PROJECT_NAME=admin
export OS_USER_DOMAIN_NAME=Default
export OS_PROJECT_DOMAIN_NAME=Default
export OS_AUTH_URL=http://$HOST_IP:5000/v3
export OS_IDENTITY_API_VERSION=3


glance image-create --name "cirros" \
  --file cirros-0.4.0-x86_64-disk.img \
  --disk-format qcow2 --container-format bare \
  --visibility=public

}

install_pkgs
conf_glance
set +e
upload_cirros_image

echo "Done!"
exit 0