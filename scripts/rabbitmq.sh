#!/usr/bin/env bash

set -o xtrace
source openstack.conf

add_openstack_user(){

rabbitmqctl add_user openstack $RABBITMQ_PASSWORD
	
rabbitmqctl set_permissions openstack ".*" ".*" ".*"

}

add_openstack_user

echo "Done!"
exit 0