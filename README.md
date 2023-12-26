# Debian OpenStack Installer
### A Devstack alternative to install an OpenStack test environment on Debian in a short time

After much effort, here it is, Debian OpenStack Installer is a series of SH scripts that allows the distribution of a minimal OpenStack environment in Debian distros
It allows you to independently deploy OpenStack in a single node

Each SH script configures a single service 

Before using this little Utility we would need the requirements

### Supported Distros:
- Ubuntu
- Pop! OS
- Q4OS
- SparkyLinux
- Zorin OS
- Kali Linux
- Linux Mint
- Elementary OS
- Any Distro that is based on Debian and has Python3+ support

### Unsupported Distros
- CentOS
- Fedora
- OpenSUSE
- Any distro that is not based on Debian but on Red Hat or OpenSUSE
(in Red Hat there is another deployment tool Packstack)

### Minimum requirements
- Distro: Any Debian-based distro
- RAM: 4 GB RAM
- CPUs: 2
- Hard Disk Capacity: 10GB

### Recommended Requirements
- Distro: Any Debian-based distro
- RAM: 8 GB RAM
- CPUs: 4
- Hard Disk Capacity: 20GB

Well, after taking a little look at the requirements for this utility we can now move on to the installation

## OpenStack Installation Steps

Install any Debian distro (I recommend the classic Ubuntu distro) on a virtual machine or on a physical machine, the distro must be clean

First of all update all the packages on your system
```
sudo su
apt update -y && apt upgrade -y
```

Install git if it isn't already installed:
```
apt install git -y
```

Now proceed to clone the Debian OpenStack Installer repo
```
cd /root
git clone https://github.com/Sorecchione07435/Debian-OpenStack-Installer.git
```

Now enter the following folder
```
cd Debian-OpenStack-Installer/
```

And before starting the OpenStack installation you first need to edit a small configuration file, open the file with nano: ```openstack.conf```
and fill in all the fields:

Specifying the values for the public network, for its subnet, also entering the IP address of your machine and the passwords for the administrator, demo user, all services, databases, and RabbitMQ and the OpenStack release

After making your configuration, save the file

Now give yourself permission to run the main script:
```
chmod +x openstack-install.sh
```

And finally start the OpenStack installation with:
```
./openstack-install.sh
```

Now you will have to wait a few minutes, (depends on the timing of your machine), this will configure the following OpenStack services (Keystone, Glance, Placement, Nova, Neutron, Horizon)

Cinder has finally been introduced in the installer, it will be possible to install a controller node, to configure a storage node follow https://docs.openstack.org/cinder/latest/install/cinder-storage-install-ubuntu.html

**Warning!: If in case the scripts that configure each specific service are unable to create the Cirros image or the Neutron networks, there is another SH script aside to create the missing things which is: ```finalize.sh```**

After the end of the installation you will see this output:

```
*** OpenStack installation Successful ***

OpenStack installation Info
+-------------------------------------------------------------------------------------------------------------+
|	The keystone credentials RC files are stored in the /root directory										
|	The admin password is 'ADMIN_PASSWORD'														
|	The demo password is 'DEMO_PASSWORD'															
|	Keystone is serving at http://HOST_IP:5000/												
|																			
|	The Horizon dashboard is available at http://HOST_IP/dashboard										
|																			
|	The password for all services is 'SERVICE_PASSWORD', The password for all databases is 'DATABASE_PASSWORD'	
+-------------------------------------------------------------------------------------------------------------+

System Info
+-------------------------------------------------------------------------------------------------------------+
|	Linux Distro: Ubuntu																
|	Version: 22.04 jammy 																														
+-------------------------------------------------------------------------------------------------------------+
```

If during installation you noticed that the public network or internal network was not created due to a temporary neutron endpoint failure you can run the ```finalize.sh``` script, in order to create the missing networks or the missing cirros image

Now your OpenStack installation will be ready to use, You can access the Horizon dashboard from: http://yourip/dashboard, the user is 'admin' and the password is the one you entered on the ADMIN_PASSWORD directive and you will be able to make experimental use of it, launching instances, uploading new images etc., the network will not work 100%

**Please remember that this will not be a fully functional OpenStack installation**

