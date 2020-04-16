#! /bin/sh

for pid in `ps aux | grep "tstat" | awk '{print $2}'`; do  sudo kill -9 -s 2 $pid ; done

echo "stopped tstat; doing chown"

sudo chown -R $UID:$UID $HOME/output/tstat/

echo "chowned the tstat stuff"
