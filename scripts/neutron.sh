#!/usr/bin/env bash

set -o xtrace
set -e

source openstack.conf

conf_file=/etc/neutron/neutron.conf
conf_ml2=/etc/neutron/plugins/ml2/ml2_conf.ini
conf_openvswitch=/etc/neutron/plugins/ml2/openvswitch_agent.ini
conf_linuxbridge=/etc/neutron/plugins/ml2/linuxbridge_agent.ini
conf_dhcp_agent=/etc/neutron/dhcp_agent.ini
conf_metadata_agent=/etc/neutron/metadata_agent.ini
conf_nova=/etc/nova/nova.conf

install_pkgs(){

if  [ $NEUTRON_ML2_MECHANISM_TYPE == "linuxbridge" ]; then

apt install neutron-server neutron-plugin-ml2 neutron-linuxbridge-agent neutron-dhcp-agent neutron-metadata-agent -y

fi

if  [ $NEUTRON_ML2_MECHANISM_TYPE == "openvswitch" ]; then

apt install neutron-server neutron-plugin-ml2 neutron-openvswitch-agent neutron-dhcp-agent neutron-metadata-agent -y

fi
}

conf_neutron()
{

crudini --set $conf_file database connection mysql+pymysql://neutron:$DATABASE_PASSWORD@$HOST_IP/neutron

crudini --set $conf_file DEFAULT core_plugin ml2
crudini --set $conf_file DEFAULT transport_url rabbit://openstack:$RABBITMQ_PASSWORD@$HOST_IP
crudini --set $conf_file DEFAULT auth_strategy keystone

crudini --set $conf_file keystone_authtoken www_authenticate_uri http://$HOST_IP:5000
crudini --set $conf_file keystone_authtoken auth_url http://$HOST_IP:5000
crudini --set $conf_file keystone_authtoken memcached_servers 127.0.0.1:11211
crudini --set $conf_file keystone_authtoken auth_type password
crudini --set $conf_file keystone_authtoken project_domain_name default
crudini --set $conf_file keystone_authtoken user_domain_name default
crudini --set $conf_file keystone_authtoken project_name service
crudini --set $conf_file keystone_authtoken username neutron
crudini --set $conf_file keystone_authtoken password $SERVICE_PASSWORD

crudini --set $conf_file DEFAULT notify_nova_on_port_status_changes true
crudini --set $conf_file DEFAULT notify_nova_on_port_data_changes true

crudini --set $conf_file nova auth_url http://$HOST_IP:5000
crudini --set $conf_file nova auth_type password
crudini --set $conf_file nova project_domain_name default
crudini --set $conf_file nova user_domain_name default
crudini --set $conf_file nova region_name RegionOne
crudini --set $conf_file nova project_name service
crudini --set $conf_file nova username nova
crudini --set $conf_file nova password $SERVICE_PASSWORD

crudini --set $conf_file oslo_concurrency lock_path /var/lib/neutron/tmp

crudini --set $conf_ml2 ml2 type_drivers flat,vlan,vxlan,local
crudini --set $conf_ml2 ml2 tenant_network_types flat,vlan,local

crudini --set $conf_ml2 ml2 extension_drivers port_security
crudini --set $conf_ml2 ml2_type_flat flat_networks $HOST_IP_INTERFACE_NAME
crudini --set $conf_ml2 securitygroup enable_ipset true

if  [ $NEUTRON_ML2_MECHANISM_TYPE == "openvswitch" ]; then
crudini --set $conf_ml2 ml2 mechanism_drivers openvswitch

crudini --set $conf_openvswitch ovs bridge_mappings provider:$HOST_IP_INTERFACE_NAME
crudini --set $conf_openvswitch securitygroup enable_security_group true
crudini --set $conf_openvswitch firewall_driver openvswitch

crudini --set $conf_dhcp_agent DEFAULT interface_driver openvswitch
crudini --set $conf_dhcp_agent DEFAULT dhcp_driver neutron.agent.linux.dhcp.Dnsmasq
crudini --set $conf_dhcp_agent DEFAULT enable_isolated_metadata true

fi

if  [ $NEUTRON_ML2_MECHANISM_TYPE == "linuxbridge" ]; then

crudini --set $conf_ml2 ml2 mechanism_drivers linuxbridge

crudini --set $conf_linuxbridge linux_bridge physical_interface_mappings provider:$HOST_IP_INTERFACE_NAME
crudini --set $conf_linuxbridge vxlan enable_vxlan false
crudini --set $conf_linuxbridge securitygroup enable_security_group true
crudini --set $conf_linuxbridge firewall_driver neutron.agent.linux.iptables_firewall.IptablesFirewallDriver

crudini --set $conf_dhcp_agent DEFAULT interface_driver linuxbridge
crudini --set $conf_dhcp_agent DEFAULT dhcp_driver neutron.agent.linux.dhcp.Dnsmasq
crudini --set $conf_dhcp_agent DEFAULT enable_isolated_metadata true

fi

crudini --set $conf_metadata_agent DEFAULT nova_metadata_host $HOST_IP
crudini --set $conf_metadata_agent DEFAULT metadata_proxy_shared_secret $SERVICE_PASSWORD

crudini --set $conf_nova neutron auth_url http://$HOST_IP:5000
crudini --set $conf_nova neutron auth_type password
crudini --set $conf_nova neutron project_domain_name default
crudini --set $conf_nova neutron user_domain_name default
crudini --set $conf_nova neutron region_name RegionOne
crudini --set $conf_nova neutron project_name service
crudini --set $conf_nova neutron username neutron
crudini --set $conf_nova neutron password $SERVICE_PASSWORD
crudini --set $conf_nova neutron service_metadata_proxy true
crudini --set $conf_nova neutron metadata_proxy_shared_secret $SERVICE_PASSWORD

su -s /bin/sh -c "neutron-db-manage --config-file /etc/neutron/neutron.conf --config-file /etc/neutron/plugins/ml2/ml2_conf.ini upgrade head" neutron

systemctl restart nova-api

if  [ $NEUTRON_ML2_MECHANISM_TYPE == "openvswitch" ]; then
systemctl restart neutron-server neutron-openvswitch-agent neutron-dhcp-agent neutron-metadata-agent nova-compute
fi

if  [ $NEUTRON_ML2_MECHANISM_TYPE == "linuxbridge" ]; then
systemctl restart neutron-server neutron-linuxbridge-agent neutron-dhcp-agent neutron-metadata-agent nova-compute
fi

}

create_public_network(){

export OS_USERNAME=admin
export OS_PASSWORD=$ADMIN_PASSWORD
export OS_PROJECT_NAME=admin
export OS_USER_DOMAIN_NAME=Default
export OS_PROJECT_DOMAIN_NAME=Default
export OS_AUTH_URL=http://$HOST_IP:5000/v3
export OS_IDENTITY_API_VERSION=3

openstack network create --share --external public

openstack subnet create --network public \
  --allocation-pool start=$PUBLIC_SUBNET_RANGE_START,end=$PUBLIC_SUBNET_RANGE_END \
  --dns-nameserver $PUBLIC_SUBNET_DNS_SERVERS --gateway $PUBLIC_SUBNET_GATEWAY \
  --subnet-range $PUBLIC_SUBNET_CIDR public_subnet

}

create_internal_network(){

openstack network create --share internal

openstack subnet create --network internal --allocation-pool start=10.0.0.10,end=10.0.0.200 --dns-nameserver 8.8.8.8 --gateway 10.0.0.1 --subnet-range 10.0.0.0/24 internal_subnet

}

install_pkgs
conf_neutron
set +e
create_public_network
create_internal_network

NORMAL=$(tput sgr0)
YELLOW=$(tput setaf 3)

echo "${YELLOW}NOTE: If the networks or cirros image did not create successfully, try running the separate finalize.sh script${NORMAL}"

echo "Done!"
exit 0
