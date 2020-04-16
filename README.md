Important Files:

crawler.py: high level crawler information: provides how crawl will be executed, manages crawler instances, etc. Communicates with source_server and depends on state_helpers

state_helpers: defines KodiCrawler class, which manages crawl state information and provides an interface to manipulate Kodi (primarily through Kodi's json RPC)

source_server: indicates which add-on needs to be crawled next and manages experiment state. Acts as a centralized controller for the an arbitrary number of machines running crawler.py

-----------

Usage:

1. initialize source_server (for example: python source_server.py -s addon_sources.json -H test_history.json -P de-kodi/src/)

    -NOTE: if the list of add-on sources (for example, addon_sources.json) is not defined, make sure to allow source_server time to initialize the list (this takes several hours) --- set makesourcelist flag to 1 to generate (and auto-refresh on loop) the add-on source list 

2. on the machine where you want a crawl to occur (let's call this a crawl machine), run iptables.sh (if it's not been run already since last boot --- you can check the machine's currently active iptables to see if the docker traffic is being routed towards the mitmproxy port).

    -NOTE: you can have multiple crawl machines
    -NOTE: your crawl machine can be separate from the machine on which your source_server.py is running

3. on the crawl machine, run run_mitm.sh to start the mitmproxy server. give it a couple of seconds to start; it should print something to terminal when it's running, and automatically create a fresh copy of mitmdump.log (size 0 since it's new, located in de-kodi/src/crawler.dekodi/)

    -NOTE: this removes some files from the previous run; be careful not to get data mixed up / lost. I recommend playing with the crawler.py runs to get an idea of the files that are created / overwritten with each run.
    -NOTE: mitmproxy must be running the entire time your crawler.py is running on that machine or crawler.py won't work

4. on the crawl machine, run crawler.py (for example: python crawler.py -c 165.124.183.145:9999 -p repodekodi/de-kodi/src/ -n 8, if your source_server.py is running on 165.124.183.145 using port 9999 and your de-kodi dir is in $HOME/repodekodi/) to intiate the crawl. Note the parameters; most notably, n gives the number of kodi crawling docker instances allowed to run in parallel on that single machine.

