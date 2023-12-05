#!/bin/bash

BASE_DIR=$PWD
RED=$(tput setaf 1)
NORMAL=$(tput sgr0)


fatal() {
  echo "${RED}FATAL ERROR${NORMAL}: on status" $* >&2
  exit 2
}

source openstack.conf
source /etc/lsb-release
source /etc/os-release

if  $ID_LIKE == "suse opensuse"; then
echo "OpenSUSE systems are not supported with this OpenStack Deployment!"
exit
fi

if [ ! -f "/etc/debian_version" ]; then
   echo "OpenStack distribution with this utility is only supported in Debian-based distros, and not in RHEL distros"
   exit
fi


chmod +x scripts/prereqs.sh
chmod +x scripts/rabbitmq.sh
chmod +x scripts/mariadb.sh
chmod +x scripts/keystone.sh
chmod +x scripts/glance.sh
chmod +x scripts/cinder.sh
chmod +x scripts/placement.sh
chmod +x scripts/nova.sh
chmod +x scripts/nova-compute.sh
chmod +x scripts/neutron.sh
chmod +x scripts/horizon.sh
chmod +x openstack.conf
chmod +x finalize.sh

cp openstack.conf scripts/openstack.conf

clear

if [ "$EUID" -ne 0 ]
  then echo "Please run as root"
  exit
fi

echo "Welcome to the OpenStack Installer (Debian)"
echo ""

echo "Continuing installation as $(id -un)..."
echo ""

if [ $INSTALL_CINDER == "yes" ]; then

echo "NOTICE:"
echo ""
printf 'You have chosen to install the Block Storage Service (Cinder), ONLY a controller node will be installed, at the end of the OpenStack deployment you will not be able to create volumes unless you manually configure a storage node with a physical volume etc..., Proceed with the installation? (yes/no)'
read answer

if [ "$answer" != "${answer#[Yy]}" ] ;then 
    echo "1 | 11 : Configuring prereqs..."

scripts/prereqs.sh || fatal "1"

echo "2 | 11 : Configuring RabbitMQ..."

scripts/rabbitmq.sh || fatal "2"

echo "3 | 11 : Configuring MariaDB..."

scripts/mariadb.sh || fatal "3"

echo "4 | 11 : Configuring Keystone..."

scripts/keystone.sh || fatal "4"

echo "5 | 11 : Configuring Glance..."

scripts/glance.sh || fatal "5"

rm -rf $BASE_DIR/cirros-0.4.0-x86_64-disk.img

echo "6 | 11 : Configuring Cinder..."

scripts/cinder.sh || fatal "6"

echo "7 | 11 : Configuring Placement..."

scripts/placement.sh || fatal "7"

echo "8 | 11 : Configuring Nova..."

scripts/nova.sh || fatal "8"

echo "9 | 11 : Configuring Nova Compute Node..."

scripts/nova-compute.sh || fatal "9"

echo "10 | 11 : Configuring Neutron..."

scripts/neutron.sh || fatal "10"

echo "11 | 11 : Configuring Horizon..."

scripts/horizon.sh || fatal "11"
else
    exit
fi

else

echo "1 | 10 : Configuring prereqs..."

scripts/prereqs.sh || fatal "1"

echo "2 | 10 : Configuring RabbitMQ..."

scripts/rabbitmq.sh || fatal "2"

echo "3 | 10 : Configuring MariaDB..."

scripts/mariadb.sh || fatal "3"

echo "4 | 10 : Configuring Keystone..."

scripts/keystone.sh || fatal "4"

echo "5 | 10 : Configuring Glance..."

scripts/glance.sh || fatal "5"

rm -rf $BASE_DIR/cirros-0.4.0-x86_64-disk.img

echo "6 | 10 : Configuring Placement..."

scripts/placement.sh || fatal "6"

echo "7 | 10 : Configuring Nova..."

scripts/nova.sh || fatal "7"

echo "8 | 10 : Configuring Nova Compute Node..."

scripts/nova-compute.sh || fatal "8"

echo "9 | 10 : Configuring Neutron..."

scripts/neutron.sh || fatal "9"

echo "10 | 10 : Configuring Horizon..."

scripts/horizon.sh || fatal "10"

fi



echo "*** OpenStack Deploy Successful ***"
echo ""
echo "OpenStack Deployment Info"
echo "+-------------------------------------------------------------------------------------------------------------+"
echo "|	The keystone credentials RC files are stored in the /root directory											"
echo "|	The admin password is '$ADMIN_PASSWORD'																	"
echo "|	The demo password is '$DEMO_PASSWORD'																			"
echo "|	Keystone is serving at http://$HOST_IP:5000/																"
echo "|																												"
echo "|	The Horizon dashboard is available at http://$HOST_IP/dashboard												"
echo "|																												"
echo "|	The password for all services is '$SERVICE_PASSWORD', The password for all databases is '$DATABASE_PASSWORD'	"
echo "+-------------------------------------------------------------------------------------------------------------+"
echo ""
echo "System Info"
echo "+-------------------------------------------------------------------------------------------------------------+"
echo "|	Linux Distro: $DISTRIB_ID																					"
echo "|	Version: $DISTRIB_RELEASE $DISTRIB_CODENAME 																"														"
echo "+-------------------------------------------------------------------------------------------------------------+"
echo ""
echo ""

exit
