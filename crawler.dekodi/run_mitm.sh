if [ $# -ne 1 ] ; then
    sudo rm repotrees soft_fails hard_fails installed_addons completed_addons exceptions exceptions_crawler gdeps.json fruitless_sources.list error* tmpaddonsdir ../dekodi_scripts/zip_addons -r
    mkdir ../dekodi_scripts/zip_addons
fi
mv mitmdump.log ../mitmdump.$(date +%s)
t=../bak.$(date +%s)
mkdir $t
mv ../dekodi_data/* $t
cp test_history.json ../test_history.json.$(date +%s)
while true; do
    bash -c 'sleep 14400; docker kill mitmproxycontainer' &
    docker run --memory="1g" --memory-swap="1g" --name mitmproxycontainer --network host --rm -it -v $PWD/mitmproxy:/home/mitmproxy/.mitmproxy -p 8080:8080 \
        mitmproxy/mitmproxy mitmdump --mode transparent --showhost --flow-detail 2 \
        | ts '%s' \
        | sed -r "s/[[:cntrl:]]\[[0-9]{1,3}m//g" \
        | grep -P "GET|HEAD|POST|PUT|DELETE|CONNECT|OPTIONS|TRACE|User-Agent|<<|:\sclient(c|d)|Last-Modified|Server" >> mitmdump.log
    echo "restarting mitmdump"
done
