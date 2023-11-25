#!/usr/bin/env bash

set -o xtrace

source openstack.conf

add_openstack_user(){

if ! /bin/false; then
    rabbitmqctl add_user openstack $RABBITMQ_PASSWORD
	
	rabbitmqctl set_permissions openstack ".*" ".*" ".*"
fi

}

add_openstack_user

echo "Done!"
exit 0