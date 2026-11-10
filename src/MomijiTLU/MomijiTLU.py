"""
SPDX-FileCopyrightText: 2025 DESY and the Constellation authors
SPDX-License-Identifier: EUPL-1.2
"""

from enum import StrEnum
import struct
import time
import socket
from pathlib import Path
from typing import Any

from constellation.core.configuration import Configuration, enum_type
from constellation.core.transmitter_satellite import TransmitterSatellite
from constellation.core.monitoring import schedule_metric
from constellation.core.commandmanager import cscp_requestable

from . import __version__
from . import rbcp
from .registers import MomijiReg, Ctrl, Stat, N_CH_IN, N_CH_BUSY, EVT_BYTES

class Polarity(StrEnum):
    RISING = "rising"
    FALLING = "falling"

class MomijiTLU(TransmitterSatellite, version=__version__):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.file_path = Path(__file__).parent
        self.rbcp = None
        self.tcp_sock = None
        self.cfg = None
        self.fw_version = None
        self.buf = bytearray()
        self.n_sent = 0

    def do_initializing(self, config: Configuration) -> str:
        """
        initialize tlu:
            1. read FW_VERSION through RBCP register
            2. configure, setup parameter from config.setdefault(...)
            3. apply
        """
        # --- get configuration
        self.cfg = self._read_config(config)

        if self.rbcp:
            self.rbcp.close()

        self.rbcp = rbcp.Rbcp(
            ip=self.cfg['ip_hws'],
            port=self.cfg['port_rbcp'],
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
        self.write_reg_and_check(MomijiReg.DUT_MASK, self.cfg['dut_mask'])
        self.write_reg_and_check(MomijiReg.BLOCK_MASK, self.cfg['block_mask'])
        self.write_reg_and_check(MomijiReg.LUT, self.cfg['trigger_input_logic'])
        self.write_reg_and_check(MomijiReg.TRG_O_WIDTH, self.cfg['trigger_output_width'])
        self.write_reg_and_check(MomijiReg.TRG_INVERT, self.cfg['trigger_polarity'])
        self.write_reg_and_check(MomijiReg.BUSY_INVERT, self.cfg['busy_polarity'])
        self.write_reg_and_check(MomijiReg.TRG_EDGE_EN, self.cfg['trigger_edge_enable'])
        self.write_reg_and_check(MomijiReg.BUSY_TIMEOUT, self.cfg['busy_timeout'])
        self.write_reg_and_check(MomijiReg.T0_WIDTH, self.cfg['t0_width'])
        
        for i in range(n_in):
            self.write_reg_and_check(
                MomijiReg.TRG_I_WIDTH,
                self.cfg['trigger_input_width'][i],
                ch=i)
        for i in range(n_busy):
            self.write_reg_and_check(
                MomijiReg.BUSY_WIDTH,
                self.cfg['busy_input_width'][i],
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
        self._close_tcp()
        return "Do landing complete"

    def do_starting(self, run_identifier: str = None) -> str:
        """
        connect to tcp, then start run
        """
        # --- create TCP socket
        try:
            self.tcp_sock = socket.create_connection(
                (self.cfg['ip_hws'], self.cfg['port_tcp']),
                timeout = 5
                )
            except OSError as e:
                raise RuntimeError(
                f"cannot connect TCP. Old connecton still open? or the ip/port is wrong"
                ) from e
        
        # --- start trigger + T0 sync
        try:
            self.tcp_sock.settimeout(0.2)
            self.write_reg(MomijiReg.CTRL, Ctrl.RUN_START)
        except Exception:
            self._close_tcp()

        # --- reset data buffer
        self.buf = bytearray()
        self.n_sent = 0
        
        # --- set begin-of-run tags
        self.bor["firmware_version"] = self.fw_version
        self.bor["eudaq_event"] = "TluRawDataEvent"
        self.bor["write_as_blocks"] = True
        
        return "Do starting complete"
    
    def do_run(self) -> str:
        # --- receive data and send until stop
        while not self.stop_requested():
            self._receive_and_send()
        return "Do running complete"

    def do_stopping(self) -> str:
        # --- stop run
        self.write_reg(MomijiReg.CTRL, Ctrl.RUN_STOP)
        
        # --- finish processing
        t_end = time.time() + 5
        while not (self.read_reg(MomijiReg.STATUS) & Stat.EMPTY) and time.time() < t_end:
            self._receive_and_send()

        while self._receive_and_send():
            pass

        if self.buf:
            self.log.warning(f"{len(self.buf)} bytes left over (not a multiple of {EVT_BYTES})")

        # --- set end-of-run tags
        self.write_reg(MomijiReg.CTRL, Ctrl.SNAPSHOT)
        n_accept  = self.read_reg(MomijiReg.N_ACCEPT)
        n_drop    = self.read_reg(MomijiReg.N_DROP)
        n_timeout = self.read_reg(MomijiReg.N_TIMEOUT)
        n_in_hit  = [self.read_reg(MomijiReg.N_IN_HIT, ch=i) for i in range(N_CH_IN)]

        if not n_accept == self.n_sent:
            self.log.warning(f"n_accept != n_sent. Check quality")
        
        self.eor["n_accept"]  = n_accept
        self.eor["n_drop"]    = n_drop
        self.eor["n_timeout"] = n_timeout
        self.eor["n_in_hit"]  = n_in_hit

        # --- close TCP socket
        self._close_tcp()

        return "Do stopping complete"

    def write_reg_and_check(self, reg: MomijiReg, value: int, ch: int = 0) -> None:
        
        self.write_reg(reg, value, ch)
        response = self.read_reg(reg, ch)

        if response == value:
            self.log.info(f"{reg.name}[{ch}]: wrote 0x{value:02x}")
        else:
            raise RuntimeError(f"{reg.name}: wrote 0x{value:02x}, read 0x{response:02x}")

    def write_reg(self, reg: MomijiReg, value: int, ch: int = 0) -> None:
        self.rbcp.write_uint(reg.addr_at(ch), value, reg.n_bytes)
    
    def read_reg(self, reg: MomijiReg, ch: int = 0) -> int:
        return self.rbcp.read_uint(reg.addr_at(ch), reg.n_bytes)

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
            "port_rbcp": config.get_int(
                key="port_rbcp",
                default_value=4660
            ),
            "port_tcp": config.get_int(
                key="port_tcp",
                default_value=24
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
                element_type=int,
                default_value=[4] * N_CH_IN
            ),
            "t0_width": config.get_int(
                key="t0_width",
                default_value=16 # 100ns
            ),
            "busy_input_width": config.get_array(
                key="busy_input_width",
                element_type=int,
                default_value=[1] * N_CH_BUSY
            )
        }

        # self.status_interval = config.get_float("status_interval")
        # self.log.debug("Calculating status every %.3f s" % self.status_interval)

        return configuration

    def _receive_and_send(self) -> int:
        try:
            chunk = self.tcp_sock.recv(65536)
        except socket.timeout:
            return 0
        if not chunk:
            raise RuntimeError("TCP connection closed by the board")
        
        self.buf += chunk

        evt_in_buf = len(self.buf) // EVT_BYTES
        for k in range(0, evt_in_buf * EVT_BYTES, EVT_BYTES):
            self._send_event(self.buf[k:k + EVT_BYTES])

        del self.buf[:evt_in_buf * EVT_BYTES]
        self.n_sent += 1

        return len(chunk)

    def _send_event(self, evt: bytearray) -> None:
        words = struct.unpack(">6I", evt)
        timestamp = 25 * ((((words[0] & 0xFFFF) << 32) | words[1]) >> 2) # [ns]
        trigger_id = words[3]

        if words[5] != 0:
            raise RuntimeError("The data stream crashed 😡!!")

        # --- meta record
        data_record = self.new_data_record(
            {
                "flag_trigger": True,
                "trigger_number": trigger_id,
                "timestamp_begin": int(timestamp * 1000),
                "timestamp_end": int((timestamp + 25) * 1000)
            }
        )
        # --- payload
        data_record.add_block(struct.pack("<6I", *words))

        # ---- send evt data
        self.send_data_record(data_record)

    def _close_tcp(self) -> None: 
        if self.tcp_sock is not None:
            self.tcp_sock.close()
            self.tcp_sock = None