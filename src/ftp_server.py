from os import listdir, remove, getcwd, chdir, stat, mkdir, rmdir, rename
from time import monotonic, localtime, sleep
from storage import remount

_enc = "UTF-8" # We currently only support UTF-8

_msgs = [ # The message board.
    b"501 Syntax error in parameters or arguments.",  # 0
    b"230 User logged in, proceed.",  # 1
    b"331 User name okay, need password.",  # 2
    b"220 Welcome!",  # 3
    b"215 UNIX Type: L8.",  # 4
    b"550 Failed Directory not exists.",  # 5
    b"250 Command successful.",  # 6
    b"530 User not logged in.",  # 7
    b"150 Here is listing.",  # 8
    b"550 LIST failed Path name not exists.",  # 9
    b"226 List done.",  # 10
    b"200 Get port.",  # 11
    b"200 Binary mode.",  # 12
    b"200 Ascii mode.",  # 13
    b"200 Ok.",  # 14
    b"221 Goodbye!",  # 15
    b"421 Service not available.",  # 16
    b"150 Here is the file.",  # 17
    b"550 File not found",  # 18
    b"226 Transfer complete",  # 19
    b"550 Requested action not taken. File storage is not allowed on this server.",  # 20
    b"550 Directory not empty",  # 21
    b"350 File or directory exists, ready for destination name.",  # 22
    b"553 Requested action not taken. File name not allowed.",  # 23
    b"350 Ready for RNTO.",  # 24
    b"451 Requested action aborted: local error in processing.",  # 25
]


class ftp:
    def __init__(
        self,
        pool,
        ip,
        port=21,
        authlist={},
        maxcache=5,
        maxbuf=2880,
        auth_timeout=120,
        verbose=False,
    ) -> None:
        # Public
        self.pasv_port = 20  # This port will be used for pasv connections.
        self.data_ip = None
        self.data_port = None
        if auth_timeout < 0:
            raise ValueError("auth_timeout must be at least 0!")
        self.auth_timeout = auth_timeout
        self.tx_size = 2048
        """
        The file transmission speed used to send files.
        If you set it too high, packets will be cut by the network stack and it will be slower,
        due to the retries.
        """
        self.deinited = False
        self.verbose = verbose  # Print some useful logs.
        if maxcache < 2:
            raise ValueError("Cache must be at least 2 times the buffer!")
        self._max_cache = (
            maxcache  # How many times maxbuf do we store before actually writing.
        )
        self.mode = False  # False == "I", True = "A"
        self.ro = False  # Set to True to reject writes.

        # Private
        self._pool = pool
        self._socket = pool.socket(pool.AF_INET, pool.SOCK_STREAM)
        self._socket.setblocking(False)
        self._data_socket = None
        self._socket.bind((ip, port))
        self._socket.listen(2)
        self._iptup = (ip, port)
        self._conn = None
        self._client = None
        self._client_pasv = None
        self._rx_buf = bytearray(maxbuf)
        self._maxbuf = maxbuf
        self._authenticated = not bool(len(authlist))
        self._pasv = False
        self._pasv_sock = None
        self._pollt = monotonic()
        self._authlist = authlist
        self._tmpuser = None
        self._timer = None
        self._file_cache = bytearray(maxcache * maxbuf)
        self._rename_from = None

    @property
    def max_cache(self) -> int:
        """
        The maximum file cache in ram.
        Whatever value is set is then multiplied by the buffer size.
        """
        return self._max_cache

    @max_cache.setter
    def max_cache(self, value) -> None:
        if value < 2:
            raise ValueError("Cache must be at least 2 times the buffer!")
        self._max_cache = value
        self._reset_file_cache()

    @property
    def user(self):
        if self.deinited or not self.authenticated:
            return
        # Returns the connected username, if it exists.
        return self._tmpuser if self._tmpuser is not None else ""

    @property
    def pasv(self) -> None:
        # Returns True if a passive connection is active
        return self._pasv

    @property
    def authenticated(self) -> bool:
        if self.deinited:
            return
        return self._authenticated

    @property
    def connected(self) -> bool:
        # Is a client connected
        if self.deinited:
            return
        return self._conn is not None

    def disconnect(self) -> None:
        # Disconnect and clear the connections.
        if self.deinited:
            return
        self._reset_data_sock()
        if self._conn is not None:
            if self.verbose:
                print("Disconnected {}:{}".format(self.client[0], self.client[1]))
            self._conn.close()
            self._conn = None
        self._reset_rx_buffer()
        if self.pasv:
            self._pasv = False
        self._reset_data_sock()
        self._authenticated = not bool(len(self._authlist))
        chdir("/")

    @property
    def client(self):
        """
        Returns a tuple with the connected client's ip and port.
        If no connection, returns None.
        """
        if self.deinited:
            return
        return self._client if self.connected else None

    def serve_till_quit(self) -> None:
        # Run the server till a client exits.
        if self.deinited:
            return
        while not self.poll():
            pass

    def serve(self) -> None:
        # Run the server forever
        if self.deinited:
            return
        while True:
            self.poll()

    def poll(self) -> bool:
        """
        This is what runs the server. You need this to run in a while True.
        serve() and serve_till_quit() do this.

        This function returns False every time, except if on this loop, it disconnected a client.
        For this condition, both manual disconnections and lost connections count, since filezilla
        is not very kind to us.
        It will not return True for a client that was kicked due to the auth timeout.
        """
        if self.deinited:
            return False
        """
        Use this function to poll the server
        """
        res = False
        if self._ensure_conn():
            return True
        if not self._connect():
            return res
        if (not self.authenticated) and (monotonic() - self._timer > self.auth_timeout):
            self._kick(self.client)
            self.disconnect()
            return res
        try:
            size = self._conn.recv_into(self._rx_buf, self._maxbuf)
            if size:
                try:
                    raw = bytes(memoryview(self._rx_buf)[:size]).decode(_enc)
                    if self.verbose:
                        print(">" * 40 + "\n" + raw + "\n" + "<" * 40)
                    command = raw.split(" ")[0].replace("\r\n", "").lower()
                    if command == "user":
                        self._user(raw)
                    elif command == "pass":
                        self._pass(raw)
                    elif command == "syst":
                        self._syst()
                    elif command == "pwd":
                        self._pwd()
                    elif command == "cwd":
                        self._cwd(raw)
                    elif command == "cdup":
                        self._cdup()
                    elif command == "list":
                        self._list(raw)
                    elif command == "nlist":
                        self._list(raw)
                    elif command == "port":
                        self._port(raw)
                    elif command == "size":
                        self._size(raw)
                    elif command == "type":
                        self._type(raw)
                    elif command == "pasv":
                        self._enpasv()
                    elif command == "noop":
                        self._send_msg(14)
                    elif command == "retr":
                        self._retr(raw)
                    elif command == "stor":
                        self._stor(raw)
                    elif command == "dele":
                        self._dele(raw)
                    elif command == "rmd":
                        self._rmd(raw)
                    elif command == "mkd":
                        self._mkd(raw)
                    elif command == "rnfr":
                        self._rnfr(raw)
                    elif command == "rnto":
                        self._rnto(raw)
                    elif command == "appe":
                        self._stor(raw, True)
                    elif command == "quit":
                        self._send_msg(15)
                        self.disconnect()
                        res = True
                    else:
                        self._send_msg(0)
                        if self.verbose:
                            print("Unknown command:", command)
                    del raw
                except UnicodeError:
                    pass
            del size
        except OSError:
            pass
        except BrokenPipeError:
            self.disconnect()
        return res

    def deinit(self) -> None:
        # Destroy the object effectively
        if self.deinited:
            return
        self.disconnect()
        del (
            self.pasv_port,
            self.data_ip,
            self.data_port,
            self.auth_timeout,
            self.tx_size,
            self.verbose,
            self._max_cache,
            self.mode,
            self.ro,
            self._pool,
            self._socket,
            self._data_socket,
            self._iptup,
            self._conn,
            self._client,
            self._client_pasv,
            self._rx_buf,
            self._maxbuf,
            self._authenticated,
            self._pasv,
            self._pasv_sock,
            self._pollt,
            self._authlist,
            self._tmpuser,
            self._timer,
            self._file_cache,
            self._rename_from,
        )
        self.deinited = True

    # Internal functions passed this point. Do not touch. Or do. Idc.

    def _user(self, data) -> None:
        # Username reading.
        if len(self._authlist):
            user = data.split(" ")[1].replace("\r\n", "")
            if user not in self._authlist.keys():
                self._send_msg(0)
            elif self._authlist[user] is None:
                self._send_msg(1)
                self._authenticated = True
                self._tmpuser = user
                self._logon()
            else:
                self._send_msg(2)
                self._tmpuser = user
            del user
        else:
            self._send_msg(1)

    def _pass(self, data) -> None:
        # Read the password and auth if correct.
        if self.user is not None:
            passwd = data.split(" ")[1].replace("\r\n", "")
            if passwd == self._authlist[self.user]:
                self._send_msg(1)
                self._authenticated = True
                self._logon()
            else:
                self._send_msg(1)
                self.disconnect()
            del passwd
        else:
            self._send_msg(0)

    def _syst(self) -> None:
        if not self._authcheck():
            return
        self._send_msg(4)

    def _retr(self, data) -> None:
        if not self._authcheck():
            return
        filen = data.split(" ")[1].replace("\r\n", "")
        self._enable_data()
        try:
            with open(filen, "r" if self.mode else "rb") as f:
                self._send_msg(17)
                while True:
                    dat = f.read(self.tx_size) # Reading in chunks
                    if not dat:
                        del dat
                        break
                    if self.mode:
                        dat = dat.encode(_enc)
                    res = 0
                    while res != len(dat):
                        try:
                            res += self._data_socket.send(memoryview(dat)[res:])
                        except OSError:
                            pass
            self._send_msg(19)
        except OSError:
            self._send_msg(18)
        self._disable_data()

    def _stor(self, data, append=False) -> None:
        if not self._authcheck():
            return
        self._enable_data()
        try:
            if self.ro:
                raise RuntimeError
            remount("/", False)
            filen = data.split(" ")[1].replace("\r\n", "")
            mod = "w" if self.mode else "wb"
            if append:
                mod = "a" if self.mode else "ab"
                with open(filen):
                    pass  # Ensure it exists
            self._conn.send(b"150 Opening data connection for {}\r\n".format(filen))
            with open(filen, mod) as f:
                cache_stored = 0
                while True:
                    size = 0
                    try:
                        size = self._data_socket.recv_into(self._rx_buf, self._maxbuf)
                        if self._max_cache and (
                            cache_stored + size > self._max_cache * self._maxbuf
                        ):
                            f.write(bytes(memoryview(self._file_cache)[:cache_stored]))
                            cache_stored = 0
                        self._file_cache[cache_stored:size] = memoryview(self._rx_buf)[
                            :size
                        ]
                        cache_stored += size
                    except OSError:
                        try:
                            self._data_socket.send(b"")
                        except BrokenPipeError:
                            break
                if cache_stored:
                    f.write(bytes(memoryview(self._file_cache)[:cache_stored]))
            self._send_msg(19)
            remount("/", True)
        except RuntimeError:
            self._send_msg(20)
        except OSError:  # Append failed
            self._send_msg(18)
        self._disable_data()

    def _type(self, data) -> None:
        if not self._authcheck():
            return
        modeset = data.split(" ")[1].replace("\r\n", "")
        if modeset == "I":
            self.mode = False
            self._send_msg(12)
        else:  # Intentional fallback
            self.mode = True
            self._send_msg(13)

    def _size(self, data) -> None:
        if not self._authcheck():
            return
        item = data.split(" ")[1].replace("\r\n", "")
        try:
            self._conn.send(b"213 " + str(stat(item)[6]).encode(_enc) + b"\r\n")
        except OSError:
            self._conn.send(b"550 SIZE could not be detected.\r\n")

    def _cdup(self) -> None:
        if not self._authcheck():
            return
        chdir("..")
        self._send_msg(14)

    def _pwd(self) -> None:
        if not self._authcheck():
            return
        self._conn.send(b'257 "{}".\r\n'.format(getcwd()))

    def _cwd(self, data) -> None:
        if not self._authcheck():
            return
        ndr = data.split(" ")[1].replace("\r\n", "")
        try:
            chdir(ndr)
            self._send_msg(6)
        except OSError:
            self._send_msg(5)
        del ndr

    def _enpasv(self) -> None:
        if not self._authcheck():
            return
        self._pasv = True
        self._reset_data_sock()
        self._enable_data()

    def _port(self, data) -> None:
        if not self._authcheck():
            return
        self._reset_data_sock()
        self._pasv = False
        self._reset_data_sock()
        spl = data.split(" ")[1].replace("\r\n", "").split(",")
        self.data_ip = ".".join(spl[:4])
        self.data_port = (256 * int(spl[4])) + int(spl[5])
        self._send_msg(11)
        del spl

    def _list(self, data) -> None:
        if not self._authcheck():
            return
        dirl = None
        dats = data.split(" ")
        if len(dats) > 1:
            dirl = dats[1].replace("\r\n", "")
        del dats
        del data
        target = getcwd() if dirl is None else dirl
        try:
            if stat(target)[0] != 16384:
                raise OSError  # File
        except OSError:  # Does non exist
            self._send_msg(9)
            return
        listing = listdir(target)
        self._send_msg(8)
        self._enable_data()
        for i in listing:
            stati = stat(i)
            line = b""
            if stat(f"{target}/{i}")[0] == 32768:
                line += b"-rwxrwxrwx 1"
            else:
                line += b"drwxrwxrwx 2"
            line += b" nobody nobody " + str(stati[6]).encode(_enc) + b" "
            date_sr = localtime(max(min(2145916800, stati[9]), 946684800))
            del stati
            if date_sr[1] == 1:
                line += b"Jan"
            elif date_sr[1] == 2:
                line += b"Feb"
            elif date_sr[1] == 3:
                line += b"Mar"
            elif date_sr[1] == 4:
                line += b"Apr"
            elif date_sr[1] == 5:
                line += b"May"
            elif date_sr[1] == 6:
                line += b"Jun"
            elif date_sr[1] == 7:
                line += b"Jul"
            elif date_sr[1] == 8:
                line += b"Aug"
            elif date_sr[1] == 9:
                line += b"Sep"
            elif date_sr[1] == 10:
                line += b"Oct"
            elif date_sr[1] == 11:
                line += b"Nov"
            elif date_sr[1] == 12:
                line += b"Dec"
            line += b" " + str(date_sr[2]).encode(_enc) + b" "
            hr = str(date_sr[3]).encode(_enc)
            if not len(hr) - 1:
                line += "0"
            line += hr + b":"
            del hr
            mint = str(date_sr[4]).encode(_enc)
            if not len(mint) - 1:
                line += "0"
            line += mint
            del mint
            line += b" " + i.encode(_enc)
            del date_sr
            self._data_socket.send(line + b"\r\n")
        self._disable_data()
        self._send_msg(10)
        del dirl, target

    def _dele(self, data) -> None:
        if not self._authcheck():
            return
        filename = data.split(" ")[1].replace("\r\n", "")
        try:
            if self.ro:
                raise RuntimeError
            remount("/", False)
            remove(filename)
            remount("/", True)
            self._send_msg(6)  # Command successful
        except OSError:
            self._send_msg(18)  # File not found
        except RuntimeError:
            self._send_msg(20)  # Can't write

    def _rmd(self, data) -> None:
        if not self._authcheck():
            return
        dirname = data.split(" ")[1].replace("\r\n", "")
        try:
            if self.ro:
                raise RuntimeError
            remount("/", False)
            rmdir(dirname)
            remount("/", True)
            self._send_msg(6)  # Command successful
        except OSError:
            self._send_msg(5)  # Directory not found
        except RuntimeError:
            self._send_msg(20)  # Can't write

    def _mkd(self, data) -> None:
        if not self._authcheck():
            return
        dirname = data.split(" ")[1].replace("\r\n", "")
        try:
            if self.ro:
                raise RuntimeError
            remount("/", False)
            mkdir(dirname)
            remount("/", True)
            self._send_msg(6)  # Command successful
        except OSError:
            self._send_msg(5)  # Directory not found
        except RuntimeError:
            self._send_msg(20)  # Can't write

    def _rnfr(self, data) -> None:
        if not self._authcheck():
            return
        self._rename_from = data.split(" ")[1].replace("\r\n", "")
        self._send_msg(24)  # Command successful

    def _rnto(self, data) -> None:
        if not self._authcheck():
            return
        if self._rename_from == None:
            self._send_msg(0)  # Invalid request, RNFR missing
            return
        rename_to = data.split(" ")[1].replace("\r\n", "")
        try:
            if self.ro:
                raise RuntimeError
            remount("/", False)
            rename(self._rename_from, rename_to)
            remount("/", True)
            self._send_msg(6)  # Command successful
        except OSError:
            self._send_msg(18)  # File not found
        except RuntimeError:
            self._send_msg(20)  # Can't write
        finally:
            self._rename_from = None

    def _enable_data(self):  # If you are using ACTIVE, disable your firewall.
        if self.verbose:
            print("Enabling socket..")
        if self._pasv:
            if self._data_socket is None:
                self._pasv_sock = self._pool.socket(
                    self._pool.AF_INET, self._pool.SOCK_STREAM
                )
                self._pasv_sock.bind((self._iptup[0], self.pasv_port))
                self._pasv_sock.setblocking(False)
                self._pasv_sock.listen(2)
                self._conn.send(
                    b"227 Entering Passive Mode ("
                    + self._iptup[0].replace(".", ",")
                    + b","
                    + str(int(self.pasv_port) // 256).encode(_enc)
                    + b","
                    + str(int(self.pasv_port) % 256).encode(_enc)
                    + b").\r\n"
                )
                timeout = monotonic()
                while (monotonic() - timeout) < 1.2:
                    try:
                        self._data_socket, self._client_pasv = self._pasv_sock.accept()
                        self._data_socket.setblocking(False)
                        if self.verbose:
                            print("Enabled PASV.")
                        break
                    except OSError:
                        pass
                if self._data_socket is None and self.verbose:
                    print("PASV timed out!")
                    self._disable_data()
                    self._send_msg(25)
                    raise TimeoutError("Client did not connect.")

        else:
            self._data_socket.connect((self.data_ip, self.data_port))
            if self.verbose:
                print("ACTV.", end="")
        self._sock_state = True

    def _disable_data(self):
        if self.verbose:
            print("Disabled data socket")
        self._reset_data_sock()

    def _connect(self) -> bool:
        if self._conn is None:
            try:
                self._conn, self._client = self._socket.accept()
                self._conn.setblocking(False)
                self._send_msg(3)
                self._reset_rx_buffer()
                if self.verbose:
                    print(
                        "Connected client from {}:{}".format(
                            self.client[0], self.client[1]
                        )
                    )
                if not self.authenticated:
                    self._timer = monotonic()
            except OSError:  # No connection took place.
                return False
        else:
            try:
                tmpconn, tmpclient = self._socket.accept()
                self._kick(tmpclient)
                tmpconn.send(_msgs[16] + "\r\n")
                tmpconn.close()
                del tmpconn, tmpclient
            except OSError:
                pass
        return True

    def _reset_rx_buffer(self) -> None:
        if self.deinited:
            return
        for i in range(len(self._rx_buf)):
            self._rx_buf[i] = 0

    def _reset_file_cache(self) -> None:
        if self.deinited:
            return
        for i in range(self._max_cache * self._maxbuf):
            self._file_cache[i] = 0

    def _authcheck(self) -> bool:
        if not self.authenticated:
            self._send_msg(7)
        return self.authenticated

    def _logon(self) -> None:
        if self.verbose:
            print(
                "Logged in {} from {}:{}".format(
                    self._tmpuser, self.client[0], self.client[1]
                )
            )

    def _kick(self, cl) -> None:
        if self.verbose:
            print("Kicked {}:{}".format(cl[0], cl[1]))
        del cl

    def _reset_data_sock(self) -> None:
        if self.deinited:
            return
        if self._data_socket is not None:
            self._data_socket.close()
            self._data_socket = None
        if self.pasv:
            if self._pasv_sock is not None:
                self._pasv_sock.close()
                self._pasv_sock = None
        else:
            self._data_socket = self._pool.socket(
                self._pool.AF_INET, self._pool.SOCK_STREAM
            )
            self._data_socket.bind(self._iptup)
            self._data_socket.listen(1)

    def _send_msg(self, no) -> None:
        self._conn.send(_msgs[no] + b"\r\n")

    def _ensure_conn(self) -> bool:
        if not self.connected:
            return False
        try:
            if monotonic() - self._pollt > 1:
                self._conn.send(b"")
                self._pollt = monotonic()
        except:
            self.disconnect()
            return True
        return False
