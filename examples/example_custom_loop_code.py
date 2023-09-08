import wifi
from socketpool import SocketPool
from ftp import ftp
from sys import exit

wifi.radio.connect("Your_wifi_ssid_here", "Your_wifi_passwd_here")

pool = SocketPool(wifi.radio)
my_ftp_server = ftp(pool, str(wifi.radio.ipv4_address))

while True: # Customise your condition.
    my_ftp_server.poll()

my_ftp_server.deinit() # Cleanup
