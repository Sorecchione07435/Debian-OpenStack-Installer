#!/usr/bin/env bash

set -o xtrace
set -e

source openstack.conf

install_pkgs(){

apt install mariadb-server python3-pymysql -y

}

conf_mariadb(){

if [ ! -f /etc/mysql/mariadb.conf.d/99-openstack.cnf ]; then
cat >> /etc/mysql/mariadb.conf.d/99-openstack.cnf << EOF
[mysqld]
bind-address = $HOST_IP

default-storage-engine = innodb
innodb_file_per_table = on
max_connections = 4096
collation-server = utf8_general_ci
character-set-server = utf8	
EOF
fi

systemctl restart mysql

}

install_pkgs
conf_mariadb

echo "Done!"
exit 0