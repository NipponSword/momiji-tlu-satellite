"""
SPDX-FileCopyrightText: 2025 DESY and the Constellation authors
SPDX-License-Identifier: EUPL-1.2
"""

import time
from enum import IntEnum

from .i2c import I2cBus

N_CH_DAC = 4
MAX_VALUE = 0xFFF # 4096

# TODO: check if which Vref is used for MCP4728

class Vref(IntNum):
    VDD = 0
    INTERNAL = 1

class Mcp4728:
    """Mcp4728 setting class
    """
    def __init__(self, bus: I2cBus, addr: int) -> None:
        self.bus = bus
        self.addr = addr

    def set(self, ch: int, value: int, vref: Vref = Vref.INTERNAL, gain2: bool = False, eeprom: bool = False):
        """set one channel

        Args:
            ch (int): 
            value (int): value for threshold, or gain
            vref (Vref, optional): reference voltage of MCP4728 Defaults to Vref.INTERNAL.
            gain2 (bool, optional): if true, double out voltage. Defaults to False.
            eeprom (bool, optional): if true, keep it after power off. Defaults to False.
        """
        if not 0 <= ch < N_CH_DAC:
            raise ValueError(f"MCP4728 ch {ch} does not exsist")
        if not 0 <= value <= MAX_VALUE:
            raise ValueError(f"MCP4728 value {value} is out of range (0 ... {MAX_VALUE})")

        # --- make bytes
        cmd = (0x58 if eeprom else 0x40) | ch << 1
        hi = vref << 7 | int(gain2) << 4 | value >> 8
        
        # ---write
        self.bus.write(self.addr, bytes([cmd, hi, value & 0xFF]))

        if eeprom:
            time.sleep(0.1)
