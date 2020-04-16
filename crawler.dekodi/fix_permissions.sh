#!/bin/sh
sudo chown -R ${USER:=$(/usr/bin/id -run)}:$USER ../dekodi_scripts/zip_addons
