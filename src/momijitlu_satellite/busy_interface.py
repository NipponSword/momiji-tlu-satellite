from enum import StrEnum
from .trigger_board import N_CH_BUSY

class BusyInterface(StrEnum):
    HANDSHAKE = "handshake"
    VETO = "veto"
    OFF = "off"

def busy_masks(modes: list[BusyInterface]) -> tuple[int, int]:
    """create busy mask from list of BusyInterface in toml cfg

    Args:
        modes (list[BusyInterface]): setting of how to treat each busy ch

    Returns:
        tuple[int, int]: dut_mask, block mask
    """
    if len(modes) != N_CH_BUSY:
        raise ValueError(f"need {N_CH_BUSY} entries, got{len(modes)}")
    dut = sum(1 << k for k, m in enumerate(modes) if m is BusyInterface.HANDSHAKE)
    block = sum(1 << k for k, m in enumerate(modes) if m is BusyInterface.VETO)

    return dut, block
