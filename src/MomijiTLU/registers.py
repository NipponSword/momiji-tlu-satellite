"""
SPDX-FileCopyrightText: 2025 DESY and the Constellation authors
SPDX-License-Identifier: EUPL-1.2
"""

from enum import Enum, IntFlag, unique

# --- Fixed parameter，don't change
N_CH_IN    = 4
N_CH_BUSY  = 5
EVT_BYTES  = 24

# --- Register class
@unique
class MomijiReg(Enum):
#   NAME            = (addr, bytes, ch   )
    FW_VERSION      = (0x00, 4           )
    N_IN            = (0x04, 1           )
    N_BUSY          = (0x05, 1           )
    CTRL            = (0x10, 1           )
    STATUS          = (0x11, 1           )
    T0_WIDTH        = (0x12, 1           )
    TRG_INVERT      = (0x20, 1           )
    TRG_EDGE_EN     = (0x21, 1           )
    TRG_I_WIDTH     = (0x28, 1, N_CH_IN  )
    LUT             = (0x30, 8           )
    BUSY_INVERT     = (0x40, 1           )
    DUT_MASK        = (0x41, 1           )
    BLOCK_MASK      = (0x42, 1           )
    BUSY_WIDTH      = (0x48, 1, N_CH_BUSY)
    TRG_O_WIDTH     = (0x50, 1           )
    BUSY_TIMEOUT    = (0x52, 2           )
    N_ACCEPT        = (0x80, 4           )
    N_DROP          = (0x84, 4           )
    N_TIMEOUT       = (0x88, 4           )
    TS_NOW          = (0x90, 6           )
    N_IN_HIT        = (0xA0, 4, N_CH_IN  )

    def __init__ (self, addr, n_bytes=1, n_ch=1):
        self.addr = addr
        self.n_bytes = n_bytes
        self.n_ch = n_ch
    
    def addr_at(self, ch=0):
        if not 0 <= ch < self.n_ch:
            raise IndexError(f"{self.name}[{ch}]")
        return self.addr + ch * self.n_bytes

# --- Control bit address
class Ctrl(IntFlag):
    RUN_START       = 0x01
    RUN_STOP        = 0x02
    APPLY           = 0x04
    SNAPSHOT        = 0x08

# --- Status monitoring bit address
class Stat(IntFlag):
    RUNNING         = 0x01
    LOCK            = 0x02
    EMPTY           = 0x04
    ALMOST_FULL     = 0x08
    BUSY            = 0x10