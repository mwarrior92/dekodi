#! /bin/sh

for pid in `ps aux | grep "kodi-x11" | awk '{print $2}'`; do  kill -9 $pid ; done
sleep 10
$HOME/kodi/kodi-build/kodi-x11
