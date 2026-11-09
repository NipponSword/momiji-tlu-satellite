"""
SPDX-FileCopyrightText: 2025 DESY and the Constellation authors
SPDX-License-Identifier: EUPL-1.2
"""

"""RBCP client for the trigger board (HwS / SiTCP RBCP over UDP).

Packet (HwS manual Rev0.9, fig. 13), all big-endian:
  byte 0 : VER<<4 | TYPE  = 0xFF
  byte 1 : CMD<<4 | FLAG  (command: read 0xC0, write 0x80; ACK: FLAG bit3 = 1, bit0 = bus error)
  byte 2 : ID             (echoed in the ACK)
  byte 3 : LENGTH         (1-255 bytes)
  byte 4-7 : ADDRESS
  byte 8-  : DATA         (write: data to write; ACK of read: data read)

Use as a library:
    from rbcp import Rbcp
    r = Rbcp("192.168.10.16")
    print(hex(r.read32(0x00)))       # FW_VERSION
    r.write8(0x10, 0x04)             # CTRL = APPLY

or from the shell:
    python3 rbcp.py read 0x00 4
    python3 rbcp.py write 0x10 0x04
    RBCP_IP=127.0.0.1 python3 rbcp.py read 0x00 4   # other board IP
"""

# --- impoert modules
import os
import socket
import struct
import sys

# --- default configuration
DEFAULT_IP      = "192.168.10.16"
DEFAULT_PORT    = 4660

CMD_READ        = 0xC0
CMD_WRITE       = 0x80
FLAG_ACK        = 0x08
FLAG_BUS_ERR    = 0x01

# --- Exception handling
class RbcpError(Exception):
    pass

# --- RBCP
class Rbcp:
    # --- initialize 
    def __init__(self, ip=DEFAULT_IP, port=DEFAULT_PORT, timeout=1.0, retries=3):
        self.addr = (ip, port)
        self.retries = retries
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(timeout)
        self.pkt_id = 0

    # --- close socket
    def close(self):
        self.sock.close()

    # ---- one RBCP transaction (one command packet -> one ACK packet)
    def _transact(self, cmd, addr, length, data=b""):

        if not 1 <= length <= 255:
            raise ValueError("length must be 1-255")

        for _ in range(self.retries):
            self.pkt_id = (self.pkt_id + 1) & 0xFF
            pkt = struct.pack(">BBBBI", 0xFF, cmd, self.pkt_id, length, addr) + data

            # send pkt
            self.sock.sendto(pkt, self.addr)

            try:
                while True:
                    ack, _ = self.sock.recvfrom(8 + 255)
                    if len(ack) >= 8 and ack[2] == self.pkt_id:
                        break          # ignore late ACKs of earlier tries
            except socket.timeout:
                continue

            flag = ack[1]

            if not flag & FLAG_ACK:
                raise RbcpError("reply is not an ACK packet: 0x%02x" % flag)
            if flag & FLAG_BUS_ERR:
                raise RbcpError("bus error at 0x%08x (done %d of %d bytes)"
                                % (addr, ack[3], length))
            return ack[8:8 + length]

        raise RbcpError("no ACK from %s:%d (addr 0x%08x)" % (self.addr + (addr,)))

    # ---- bytes
    def read(self, addr, length=1):
        return self._transact(CMD_READ, addr, length)

    def write(self, addr, data):
        data = bytes(data)
        self._transact(CMD_WRITE, addr, len(data), data)

    # ---- integers, big-endian (register map: MSB at the lowest address)
    def read_uint(self, addr, nbytes):
        return int.from_bytes(self.read(addr, nbytes), "big")

    def write_uint(self, addr, value, nbytes):
        self.write(addr, value.to_bytes(nbytes, "big"))

    def read8(self, addr):   return self.read_uint(addr, 1)
    def read16(self, addr):  return self.read_uint(addr, 2)
    def read32(self, addr):  return self.read_uint(addr, 4)
    def read48(self, addr):  return self.read_uint(addr, 6)
    def write8(self, addr, v):  self.write_uint(addr, v, 1)
    def write16(self, addr, v): self.write_uint(addr, v, 2)


def _main(argv):

    if len(argv) < 3 or argv[1] not in ("read", "write"):
        print(__doc__)
        return 1

    r = Rbcp(os.environ.get("RBCP_IP", DEFAULT_IP))
    addr = int(argv[2], 0)

    if argv[1] == "read":
        n = int(argv[3], 0) if len(argv) > 3 else 1
        d = r.read(addr, n)
        print("0x%08x : %s  (= 0x%x)" % (addr, d.hex(" "), int.from_bytes(d, "big")))
    else:
        data = bytes(int(x, 0) for x in argv[3:])
        r.write(addr, data)
        print("0x%08x <- %s" % (addr, data.hex(" ")))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
