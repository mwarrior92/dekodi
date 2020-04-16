#! /bin/sh

echo "running tstat"
echo $PWD

addon=".kodi/addons/service.api.dekodi"

sudo $HOME/tstat/tstat/tstat -l -N $HOME/$addon/net.conf -T $HOME/$addon/runtime.conf -s $HOME/output/tstat/ -f $HOME/$addon/tcpdump.conf
