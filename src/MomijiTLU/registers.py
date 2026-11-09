from enum import IntEnum, IntFlag, StrEnum

class Addr(IntEnum):
    FW_VERSION      = 0x00
    N_IN            = 0x04
    N_BUSY          = 0x05
    CTRL            = 0x10
    STATUS          = 0x11
    T0_WIDTH        = 0x12
    TRG_INVERT      = 0x20
    TRG_EDGE_EN     = 0x21
    TRG_I_WIDTH     = 0x28
    LUT             = 0x30
    BUSY_INVERT     = 0x40
    DUT_MASK        = 0x41
    BLOCK_MASK      = 0x42
    BUSY_WIDTH      = 0x48
    TRG_O_WIDTH     = 0x50
    BUSY_TIMEOUT    = 0x52
    N_ACCEPT        = 0x80
    N_DROP          = 0x84
    N_TIMEOUT       = 0x88
    TS_NOW          = 0x90
    N_IN_HIT        = 0xA0

class Ctrl(IntFlag):
    RUN_START       = 0x01
    RUN_STOP        = 0x02
    APPLY           = 0x04
    SNAPSHOT        = 0x08

class Stat(IntFlag):
    RUNNING         = 0x01
    LOCK            = 0x02
    EMPTY           = 0x04
    ALMOST_FULL     = 0x08
    BUSY            = 0x10