#!/usr/bin/env bash

set -o xtrace
set -e

source openstack.conf

conf=/etc/keystone/keystone.conf

create_databases(){

mysql -u root -e "create database if not exists keystone"; 
mysql -u root -e "create database if not exists glance"; 
mysql -u root -e "create database if not exists placement"; 
mysql -u root -e "create database if not exists nova_api"; 
mysql -u root -e "create database if not exists nova_cell0"; 
mysql -u root -e "create database if not exists nova"; 
mysql -u root -e "create database if not exists neutron"; 

mysql -u root -e "GRANT ALL PRIVILEGES ON keystone.* TO 'keystone'@'localhost' IDENTIFIED BY '$DATABASE_PASSWORD'"; 
mysql -u root -e "GRANT ALL PRIVILEGES ON keystone.* TO 'keystone'@'%' IDENTIFIED BY '$DATABASE_PASSWORD'"; 

mysql -u root -e "GRANT ALL PRIVILEGES ON glance.* TO 'glance'@'localhost' IDENTIFIED BY '$DATABASE_PASSWORD'"; 
mysql -u root -e "GRANT ALL PRIVILEGES ON glance.* TO 'glance'@'%' IDENTIFIED BY '$DATABASE_PASSWORD'"; 

mysql -u root -e "GRANT ALL PRIVILEGES ON placement.* TO 'placement'@'localhost' IDENTIFIED BY '$DATABASE_PASSWORD'"; 
mysql -u root -e "GRANT ALL PRIVILEGES ON placement.* TO 'placement'@'%' IDENTIFIED BY '$DATABASE_PASSWORD'"; 

mysql -u root -e "GRANT ALL PRIVILEGES ON nova_api.* TO 'nova'@'localhost' IDENTIFIED BY '$DATABASE_PASSWORD'"; 
mysql -u root -e "GRANT ALL PRIVILEGES ON nova_api.* TO 'nova'@'%' IDENTIFIED BY '$DATABASE_PASSWORD'"; 

mysql -u root -e "GRANT ALL PRIVILEGES ON nova.* TO 'nova'@'localhost' IDENTIFIED BY '$DATABASE_PASSWORD'"; 
mysql -u root -e "GRANT ALL PRIVILEGES ON nova.* TO 'nova'@'%' IDENTIFIED BY '$DATABASE_PASSWORD'"; 

mysql -u root -e "GRANT ALL PRIVILEGES ON nova_cell0.* TO 'nova'@'localhost' IDENTIFIED BY '$DATABASE_PASSWORD'"; 
mysql -u root -e "GRANT ALL PRIVILEGES ON nova_cell0.* TO 'nova'@'%' IDENTIFIED BY '$DATABASE_PASSWORD'"; 

mysql -u root -e "GRANT ALL PRIVILEGES ON neutron.* TO 'neutron'@'localhost' IDENTIFIED BY '$DATABASE_PASSWORD'"; 
mysql -u root -e "GRANT ALL PRIVILEGES ON neutron.* TO 'neutron'@'%' IDENTIFIED BY '$DATABASE_PASSWORD'"; 

}

install_pkgs(){

apt install keystone -y

}

conf_keystone(){

crudini --set $conf database connection mysql+pymysql://keystone:$DATABASE_PASSWORD@$HOST_IP/keystone
crudini --set $conf token provider fernet

su -s /bin/sh -c "keystone-manage db_sync" keystone

keystone-manage fernet_setup --keystone-user keystone --keystone-group keystone
keystone-manage credential_setup --keystone-user keystone --keystone-group keystone

keystone-manage bootstrap --bootstrap-password $ADMIN_PASSWORD \
  --bootstrap-admin-url http://$HOST_IP:5000/v3/ \
  --bootstrap-internal-url http://$HOST_IP:5000/v3/ \
  --bootstrap-public-url http://$HOST_IP:5000/v3/ \
  --bootstrap-region-id RegionOne

systemctl restart apache2

}

create_projects_and_demo_user(){

export OS_USERNAME=admin
export OS_PASSWORD=$ADMIN_PASSWORD
export OS_PROJECT_NAME=admin
export OS_USER_DOMAIN_NAME=Default
export OS_PROJECT_DOMAIN_NAME=Default
export OS_AUTH_URL=http://$HOST_IP:5000/v3
export OS_IDENTITY_API_VERSION=3

if ! /bin/false; then

openstack project create --domain default --description "Service Project" service
openstack project create --domain default --description "Demo Project" demo

openstack user create --domain default --password $DEMO_PASSWORD demo

openstack role create user

openstack role add --project demo --user demo user
 
fi

}

create_services_users(){

openstack user create --domain default --password $SERVICE_PASSWORD glance
openstack user create --domain default --password $SERVICE_PASSWORD placement
openstack user create --domain default --password $SERVICE_PASSWORD nova
openstack user create --domain default --password $SERVICE_PASSWORD neutron

openstack service delete image
openstack service delete placement
openstack service delete compute
openstack service delete network

openstack service create --name glance --description "OpenStack Image" image
openstack service create --name placement --description "Placement API" placement
openstack service create --name nova --description "OpenStack Compute" compute
openstack service create --name neutron --description "OpenStack Networking" network

openstack role add --project service --user glance admin
openstack role add --project service --user placement admin
openstack role add --project service --user nova admin
openstack role add --project service --user neutron admin

}

create_services_endpoints(){

openstack endpoint create --region RegionOne image public http://$HOST_IP:9292
openstack endpoint create --region RegionOne image internal http://$HOST_IP:9292
openstack endpoint create --region RegionOne image admin http://$HOST_IP:9292

openstack endpoint create --region RegionOne placement public http://$HOST_IP:8778
openstack endpoint create --region RegionOne placement internal http://$HOST_IP:8778
openstack endpoint create --region RegionOne placement admin http://$HOST_IP:8778

openstack endpoint create --region RegionOne compute public http://$HOST_IP:8774/v2.1
openstack endpoint create --region RegionOne compute internal http://$HOST_IP:8774/v2.1
openstack endpoint create --region RegionOne compute admin http://$HOST_IP:8774/v2.1

openstack endpoint create --region RegionOne network public http://$HOST_IP:9696
openstack endpoint create --region RegionOne network internal http://$HOST_IP:9696
openstack endpoint create --region RegionOne network admin http://$HOST_IP:9696
  
}

create_environment_scripts(){

if [ ! -f /root/admin-openrc.sh ]; then
cat >> /root/admin-openrc.sh << EOF
export OS_PROJECT_DOMAIN_NAME=Default
export OS_USER_DOMAIN_NAME=Default
export OS_PROJECT_NAME=admin
export OS_USERNAME=admin
export OS_PASSWORD=$ADMIN_PASSWORD
export OS_AUTH_URL=http://$HOST_IP:5000/v3
export OS_IDENTITY_API_VERSION=3
export OS_IMAGE_API_VERSION=2
EOF
fi

if [ ! -f /root/demo-openrc.sh ]; then
cat >> /root/demo-openrc.sh << EOF
export OS_PROJECT_DOMAIN_NAME=Default
export OS_USER_DOMAIN_NAME=Default
export OS_PROJECT_NAME=demo
export OS_USERNAME=demo
export OS_PASSWORD=$DEMO_PASSWORD
export OS_AUTH_URL=http://$HOST_IP:5000/v3
export OS_IDENTITY_API_VERSION=3
export OS_IMAGE_API_VERSION=2
EOF
fi

}

create_databases
install_pkgs
conf_keystone
set +e
create_projects_and_demo_user
create_services_users
create_services_endpoints
create_environment_scripts

echo "Done!"
exit 0