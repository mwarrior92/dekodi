sudo sysctl -w net.ipv4.ip_forward=1
sudo iptables -t nat -A PREROUTING -i docker0 -p tcp --dport 80 -j REDIRECT --to-port 8080
sudo iptables -t nat -A PREROUTING -i docker0 -p tcp --dport 443 -j REDIRECT --to-port 8080
