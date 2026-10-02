#!/usr/bin/env bash

echo 'Start Oracle Instant Client installation!'

# OS packages necessary for Oracle instant client
apt-get update && export DEBIAN_FRONTEND=noninteractive && \
apt-get -y install --no-install-recommends libaio1 curl unzip && \
apt-get -y clean

# Add Oracle instant client location to Path
export PATH=/opt/oracle/instantclient_21_6:$PATH

# Set LD Library Path
export LD_LIBRARY_PATH=/opt/oracle/instantclient_21_6${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
echo "export LD_LIBRARY_PATH=$LD_LIBRARY_PATH" >> /etc/bash.bashrc

# Set Oracle language settings
export NLS_LANG="AMERICAN_AMERICA.AL32UTF8"
echo "export NLS_LANG=$NLS_LANG" >> /etc/bash.bashrc

# Install Oracle instant client
echo 'Installing Oracle Instant Client ...'
mkdir -p /opt/oracle && rm -rf /opt/oracle/* && cd /opt/oracle && \
curl -SL "https://download.oracle.com/otn_software/linux/instantclient/216000/instantclient-basic-linux.x64-21.6.0.0.0dbru.zip" -o instant_client.zip && \
unzip instant_client.zip && rm instant_client.zip

# If Oracle Instant Client is the only Oracle software installed on the system, Update the runtime link path
echo 'Updating runtime link path ...'
echo /opt/oracle/instantclient_21_6 > /etc/ld.so.conf.d/oracle-instantclient.conf && ldconfig

echo "Done!"
