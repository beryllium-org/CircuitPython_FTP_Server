import wifi
from socketpool import SocketPool
from ftp_server import ftp
from sys import exit

wifi.radio.connect("Your_wifi_ssid_here", "Your_wifi_passwd_here")

pool = SocketPool(wifi.radio)
my_ftp_server = ftp(pool, str(wifi.radio.ipv4_address))

my_ftp_server.serve() # This will run forever
