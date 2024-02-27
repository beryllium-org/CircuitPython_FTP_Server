# CircuitPython FTP Server
A simple FTP server for Circuitpython 8.x and 9.x, with PASV and ACTIVE support!<br />
<br />
<b>Installation:</b><br /><br />
You can copy the 'src/ftp.py' to your board directly or instead make it an mpy package.<br />
Just run `make mpy`. If your board is attached, the mpy-cross used will be based off of your board's CircuitPython version.<br />
You can also override the version like: `MPYVER=9.0.0-alpha.1-32-g0928a95bb2 make mpy`<br />
<br />
<b>Usage:</b><br /><br />
Usage examples provided in 'examples'.<br />
Due to ongoing issue https://github.com/adafruit/circuitpython/issues/8363, this implementation cannot be used along with the web-workflow!
