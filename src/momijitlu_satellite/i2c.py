"""
SPDX-FileCopyrightText: 2025 DESY and the Constellation authors
SPDX-License-Identifier: EUPL-1.2
"""

from collections.abs import Callable
from enum import IntFlag

class I2cIo(IntFlag):
    """bits of I2C_IO: wite = pull low, read bit 1..0 = pin level"""

    NONE = 0x00
    SDA = 0x01
    SCL = 0x02

class I2cError(RuntimeError):
    """no ACK from slaves"""

class I2cBus:
    """I2C master on top of two func: write/read"""

    def __init__(self, write_io: Callable[[int], None], read_io: Callable[[], [int]]) -> None:
        self._write = write_io
        self._read = read_io

    def _pull(self, low: I2cIo = I2cIo.NONE) -> None:
        """line level helper, pull I2cIo

        Args:
            low (I2cIo, optional): which line to pull low. Defaults to I2cIo.NONE.
                                   i.e. both high
        """
        self._write(int(low))

    def _sda_level(self) -> int:
        """line level helper, read SDA

        Returns:
            int: SDA level
        """
        return self._read() & I2cIo.SDA

    def start(self) -> None:
        """start I2C"""
        self._pull()                        # 1. both high
        self._pull(I2cIo.SDA)               # 2. SDA low
        self._pull(I2cIo.SDA | I2cIo.SCL)   # 3. both low

    def stop(self) -> None:
        """stop I2C"""
        self._pull(I2cIo.SDA | I2cIo.SCL)   # 1. both low
        self._pull(I2cIo.SDA)               # 2. SCL high
        self._pull()                        # 3. both high (SDA rise during SCL high)

    def write_byte(self, byte: int) -> bool:
        """send 8 bits MSB first

        Args:
            byte (int): input byte

        Returns:
            bool: True if the slave ACKed
        """
        for k in range (7, -1, -1):
            sda = I2cIo.None if byte >> k & 1 else I2cIo.SDA
            # --- scl: low -> high -> low, sda: pulled
            self._pull(I2cIo.SCL | sda)
            self._pull(sda)
            self._pull(I2cIo.SCL | sda)

        # --- confirm ACK from slave
        self._pull(I2cIo.SCL)
        self._pull()
        acked = self._sda_level() == 0
        self._pull(I2cIo.SCL)
        
        return acked

    def read_byte(self, ack: bool) -> int:
        """read 8 bits MSB first from slave, then ACK or Not ACK (last byte)

        Returns:
            int: read bytes
        """

        byte = 0
        for _ in range(8):
            # --- scl: low -> high -> read -> low, sda: released
            self._pull(I2cIo.SCL)
            self._pull()
            byte = byte << 1 | (1 if self._sda_level() else 0)
            self._pull(I2cIo.SCL)

        # --- reutrn ACK or NACK
        sda = I2cIo.SDA if ack else I2cIo.NONE
        self._pull(I2cIo.SCL | sda)
        self._pull(sda)
        self._pull(I2cIo.SCL | sda)

        return byte

    def recover(self) -> None:
        """make slave release its sda"""

        # --- send 9 scl with sda released
        for _ in range(9):
            self._pull(I2cIo.SCL)
            self._pull()

        # --- stop
        self._pull(I2cIo.SCL)
        self.stop()

    def write(self, addr: int, data: bytes) -> None:
        """START, addr (write), data..., STOP

        Args:
            addr (int): I2C slave address
            data (bytes): SDA data

        Raises:
            I2cError: if any byte gets no ack
        """

        self.start()
        try:
            for i, byte in enumerate((addr << 1, *data)):
                if not self.write_byte(byte):
                    what = "address" if i == 0 else f"byte {i}"
                    raise I2cError(f"I2c 0x{addr:02x} : no ACK for {what}")
        finally:
            self.stop()

    def read(self, addr: int, n: int) -> bytes:
        """START, addr (read), bytes, STOP

        Args:
            addr (int): I2C slave address
            n (int): number of bytes

        Raises:
            I2cError: if not ACK for addr check
        """
        
        self.start()
        try:
            if not self.write_byte(addr << 1 | 1):
                raise I2cError(f"I2C 0x{addr:02x}: no ACK for address (read)")
            return bytes(self.read_byte(ack = i < n - 1) for i in range(n))
        finally:
            self.stop()

    def scan(self, addrs=range(0x08, 0x78)) -> list[int]:
        """addresses that ACK"""

        found = []
        for addr in addrs:
            self.start()
            if self.write_byte(addr << 1):
                found.append(addr)
            self.stop()

        return found
