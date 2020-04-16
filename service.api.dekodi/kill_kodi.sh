#! /bin/sh

for pid in `ps aux | grep "kodi-x11" | awk '{print $2}'`; do sudo kill -9 $pid ; done
