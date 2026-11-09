"""
SPDX-FileCopyrightText: 2025 DESY and the Constellation authors
SPDX-License-Identifier: EUPL-1.2
"""

from collections import deque
from enum import StrEnum
import os
import threading
import time
import socket
from pathlib import Path
from typing import Any

import numpy as np

from constellation.core.configuration import Configuration, enum_type
from constellation.core.transmitter_satellite import TransmitterSatellite
from constellation.core.monitoring import schedule_metric
from constellation.core.commandmanager import cscp_requestable

from . import __version__
from . import rbcp
from .registers import Addr, Ctrl, Stat

class Polarity(StrEnum):
    RISING = "rising"
    FALLING = "falling"

class MomijiTLU(TransmitterSatellite, version=__version__):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.file_path = Path(__file__).parent
        self.rbcp = None
        self.fw_version = None

    def do_initializing(self, config: Configuration) -> str:
        """
        initialize tlu:
            1. read FW_VERSION through RBCP register
            2. configure, setup parameter from config.setdefault(...)
            3. apply
        """

        cfg = self._read_config(config)

        if self.rbcp:
            self.rbcp.close()

        self.rbcp = rbcp.Rbcp(
            ip=cfg["ip_hws"],
            port=cfg["port_hws"],
        )

        self.fw_version = self.rbcp.read32(Addr.FW_VERSION)
        self.log.info(f"FW_VERSION 0x{self.fw_version:08x}")

        # --- write configuration to RBCP regs
        self._write_reg_and_check(Addr.DUT_MASK, cfg['dut_mask'])

        return "Do initializing complete"

    def do_launching(self) -> str:
        """
        do not anything
        """
        return "Do launching complete"

    def do_landing(self) -> str:
        """
        close TCP sockets
        """
        return "Do landing complete"

    def do_starting(self, run_identifier: str = None) -> str:
        """
        connect to tcp, then start run
        """
        return "Do starting complete"
    
    def do_run(self) -> str:
        return "Do running complete"

    def do_stopping(self) -> str:
        return "Do stopping complete"

    def _read_config(self, config: Configuration) -> dict[str, Any]:
        """
        Reads and checks Constellation configuration
        """
        configuration = {
            "ip_hws": config.get(
                key="ip_hws",
                default_value="192.168.10.16"
            ),
            "port_hws": config.get_int(
                key="port_hws",
                default_value= 4660
            ),
            "dut_mask": config.get_int(
                key="dut_mask",
                default_value=0x01
            ),
            "block_mask": config.get_int(
                key="block_mask",
                default_value=0x00
            ),
            "trigger_input_logic": config.get_int(
                key="trigger_input_logic",
                default_value=0xAAAA
            ),
            "trigger_output_width": config.get_int(
                key="trigger_output_width",
                default_value=16 # 100ns
            ),
            "trigger_polarity": config.get_array(
                key="trigger_polarity",
                element_type=enum_type(Polarity),
                default_value=[Polarity.RISING] *4
            ),
            "busy_polarity": config.get_array(
                key="busy_polarity",
                element_type=enum_type(Polarity),
                default_value=[Polarity.RISING] *5
            ),
            "busy_timeout": config.get_int(
                key="busy_timeout",
                default_value=0xFFFF
            ),
            "trigger_input_width": config.get_int(
                key="trigger_input_width",
                default_value=4
            ),
            "t0_width": config.get_int(
                key="t0_width",
                default_value=16 # 100ns
            ),
            "busy_input_width": config.get_int(
                key="busy_input_width",
                default_value=1
            )
        }

        # self.status_interval = config.get_float("status_interval")
        # self.log.debug("Calculating status every %.3f s" % self.status_interval)

        return configuration

    def _write_reg_and_check(self, address: Addr, value: int, bit: int = 8) -> bool:
        
        check = False

        match bit:
            case 8:
                self.rbcp.write8(address, value)
                response = self.rbcp.read8(address)
            case 16:
                self.rbcp.write16(address, value)
                response = self.rbcp.read16(address)
            case 64:
                self.rbcp.write(address, value.to_bytes(8, "big"))
                response = self.rbcp.read64(address)
            case _:
                raise ValueError("Incorrect bit width")
        
        check = (value == response)

        if check:
            self.log.info(f"Write {value} to {address.name}")
        else:
            raise RuntimeError(f"{address.name}: wrote 0x{value:02x}, read 0x{response:02x}")

        return check