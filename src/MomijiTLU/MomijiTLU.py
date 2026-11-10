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
from .registers import MomijiReg, Ctrl, Stat, N_CH_IN, N_CH_BUSY

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
        # --- get configuration
        cfg = self._read_config(config)

        if self.rbcp:
            self.rbcp.close()

        self.rbcp = rbcp.Rbcp(
            ip=cfg['ip_hws'],
            port=cfg['port_hws'],
        )

        # --- check firmware settings
        self.fw_version = self.read_reg(MomijiReg.FW_VERSION)
        n_in            = self.read_reg(MomijiReg.N_IN)
        n_busy          = self.read_reg(MomijiReg.N_BUSY)

        if (n_in, n_busy) != (N_CH_IN, N_CH_BUSY):
            raise RuntimeError(f"FW has N_IN={n_in}, N_BUSY={n_busy}, satellite expects {N_CH_IN}/{N_CH_BUSY}")

        self.log.info(f"FW_VERSION 0x{self.fw_version:08x}")
        self.log.info(f"N_IN       {n_in}")
        self.log.info(f"N_BUSY     {n_busy}")
        self.log.info(f"ID check PASSED 😊")

        # --- write configuration to RBCP regs
        self.write_reg_and_check(MomijiReg.DUT_MASK, cfg['dut_mask'])
        self.write_reg_and_check(MomijiReg.BLOCK_MASK, cfg['block_mask'])
        self.write_reg_and_check(MomijiReg.LUT, cfg['trigger_input_logic'])
        self.write_reg_and_check(MomijiReg.TRG_O_WIDTH, cfg['trigger_output_width'])
        self.write_reg_and_check(MomijiReg.TRG_INVERT, cfg['trigger_polarity'])
        self.write_reg_and_check(MomijiReg.BUSY_INVERT, cfg['busy_polarity'])
        self.write_reg_and_check(MomijiReg.BUSY_TIMEOUT, cfg['busy_timeout'])
        self.write_reg_and_check(MomijiReg.T0_WIDTH, cfg['t0_width'])
        
        for i in range(n_in):
            self.write_reg_and_check(
                MomijiReg.TRG_I_WIDTH,
                cfg['trigger_input_width'][i],
                ch=i)
        for i in range(n_busy):
            self.write_reg_and_check(
                MomijiReg.BUSY_WIDTH,
                cfg['busy_input_width'][i],
                ch=i)

        self.log.info(f"All configuration have been written 😊")

        # --- apply 
        self.write_reg(MomijiReg.CTRL, Ctrl.APPLY)
        self.log.info(f"APPLY")
        
        # --- complete initializing
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
        def _to_mask(polarities, width):
            mask = sum(
                1 << i for i, p in enumerate(polarities) if p == Polarity.FALLING
            )
            if len(polarities) != width:
                self.log.warning(f"polarities :{polarities}, bit width: {width}, mask: 0x{mask:08x}")
            return mask

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
            "trigger_polarity": _to_mask(config.get_array(
                key="trigger_polarity",
                element_type=enum_type(Polarity),
                default_value=[Polarity.RISING] * N_CH_IN
            ), N_CH_IN),
            "busy_polarity": _to_mask(config.get_array(
                key="busy_polarity",
                element_type=enum_type(Polarity),
                default_value=[Polarity.RISING] * N_CH_BUSY
            ), N_CH_BUSY),
            "trigger_edge_enable": config.get_int(
                key="trigger_edge_enable",
                default_value=0x0F
            ),
            "busy_timeout": config.get_int(
                key="busy_timeout",
                default_value=0xFFFF
            ),
            "trigger_input_width": config.get_array(
                key="trigger_input_width",
                default_value=[4] * N_CH_IN
            ),
            "t0_width": config.get_int(
                key="t0_width",
                default_value=16 # 100ns
            ),
            "busy_input_width": config.get_array(
                key="busy_input_width",
                default_value=[1] * N_CH_BUSY
            )
        }

        # self.status_interval = config.get_float("status_interval")
        # self.log.debug("Calculating status every %.3f s" % self.status_interval)

        return configuration

    def write_reg_and_check(self, reg: MomijiReg, value: int, ch: int = 0) -> None:
        
        self.write_reg(reg, value, ch)
        response = self.read_reg(reg, ch)

        if response == value:
            self.log.info(f"{reg.name}[{ch}]: wrote 0x{value}:02x")
        else:
            raise RuntimeError(f"{reg.name}: wrote 0x{value:02x}, read 0x{response:02x}")

    def write_reg(self, reg: MomijiReg, value: int, ch: int = 0) -> None:
        self.rbcp.write_uint(reg.addr_at(ch), value, reg.n_bytes)
    
    def read_reg(self, reg: MomijiReg, ch: int = 0) -> int:
        return self.rbcp.read_uint(reg.addr_at(ch), reg.n_bytes)