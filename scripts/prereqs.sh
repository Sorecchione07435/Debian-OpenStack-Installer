#!/usr/bin/env bash

set -o xtrace
source openstack.conf

set_openstack_release(){

add-apt-repository cloud-archive:$OPENSTACK_RELEASE -y

}

install_pkgs(){

apt update -y
apt install crudini wget python3-openstackclient rabbitmq-server memcached -y

}


set_openstack_release
set -e
install_pkgs

echo "Done!"
exit 0
