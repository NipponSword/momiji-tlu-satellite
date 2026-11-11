"""
SPDX-FileCopyrightText: 2025 DESY and the Constellation authors
SPDX-License-Identifier: EUPL-1.2
"""

from enum import StrEnum
import struct
import time
import socket
import threading
from pathlib import Path
from typing import Any

from constellation.core.configuration import Configuration, enum_type
from constellation.core.transmitter_satellite import TransmitterSatellite
from constellation.core.monitoring import schedule_metric
from constellation.core.protocol.cscp1 import SatelliteState
# from constellation.core.commandmanager import cscp_requestable

from . import __version__
from . import rbcp
from .registers import MomijiReg, Ctrl, Stat, N_CH_IN, N_CH_BUSY, EVT_BYTES


class Polarity(StrEnum):
    """Polarity definition"""

    RISING = "rising"
    FALLING = "falling"


class MomijiTLU(TransmitterSatellite, version=__version__):
    """Momiji-TLU satellite

    Main class of Momiji TLU, enabling to control the TLU, receive data, and send data

    Attributes:
        fw_version (int): firmware version of the board
        n_sent (int): the number of data sent to receiver satellite
        cfg (dict): dict derived from constellation.core.configuration
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # --- members
        self.file_path = Path(__file__).parent
        self.n_sent = 0
        self.rbcp = None
        self.tcp_sock = None
        self.cfg = None
        self.fw_version = None

        # --- secret
        self._counters = {}
        self._rate_prev = {}
        self._counters_time = 0
        self._rbcp_lock = threading.RLock()
        self._buf = bytearray()

        # --- register ch-by-ch metric
        for ch in range(N_CH_IN):
            self.register_scheduled_metric(
                name=f"input_rate_{ch}",
                unit="Hz",
                description=f"Hit rate of trigger input {ch}",
                interval=2,
                value_cb=lambda key="n_in_hit", ch=ch: self._rate(key, ch),
                allowed_states=[SatelliteState.RUN],
            )

    # =========================================
    # Constellation derived methods
    # =========================================
    def do_initializing(self, config: Configuration) -> str:
        """initializing, get configuration and apply

        Args:
            config (Configuration): toml configuration

        Raises:
            RuntimeError: firmware ID is not same as expected

        Returns:
            str: "Do initializing complete"
        """

        # --- get configuration
        self.cfg = self._read_config(config)

        if self.rbcp:
            self.rbcp.close()

        self.rbcp = rbcp.Rbcp(
            ip=self.cfg["ip_hws"],
            port=self.cfg["port_rbcp"],
        )

        # --- check firmware settings
        self.fw_version = self.read_reg(MomijiReg.FW_VERSION)
        n_in = self.read_reg(MomijiReg.N_IN)
        n_busy = self.read_reg(MomijiReg.N_BUSY)

        if (n_in, n_busy) != (N_CH_IN, N_CH_BUSY):
            raise RuntimeError(f"FW has N_IN={n_in}, N_BUSY={n_busy}, satellite expects {N_CH_IN}/{N_CH_BUSY}")

        self.log.info(f"FW_VERSION 0x{self.fw_version:08x}")
        self.log.info(f"N_IN       {n_in}")
        self.log.info(f"N_BUSY     {n_busy}")
        self.log.info("ID check PASSED 😊")

        # --- write configuration to RBCP regs
        self.write_reg_and_check(MomijiReg.DUT_MASK, self.cfg["dut_mask"])
        self.write_reg_and_check(MomijiReg.BLOCK_MASK, self.cfg["block_mask"])
        self.write_reg_and_check(MomijiReg.LUT, self.cfg["trigger_input_logic"])
        self.write_reg_and_check(MomijiReg.TRG_O_WIDTH, self.cfg["trigger_output_width"])
        self.write_reg_and_check(MomijiReg.TRG_INVERT, self.cfg["trigger_polarity"])
        self.write_reg_and_check(MomijiReg.BUSY_INVERT, self.cfg["busy_polarity"])
        self.write_reg_and_check(MomijiReg.TRG_EDGE_EN, self.cfg["trigger_edge_enable"])
        self.write_reg_and_check(MomijiReg.BUSY_TIMEOUT, self.cfg["busy_timeout"])
        self.write_reg_and_check(MomijiReg.T0_WIDTH, self.cfg["t0_width"])

        for i in range(n_in):
            self.write_reg_and_check(MomijiReg.TRG_I_WIDTH, self.cfg["trigger_input_width"][i], ch=i)
        for i in range(n_busy):
            self.write_reg_and_check(MomijiReg.BUSY_WIDTH, self.cfg["busy_input_width"][i], ch=i)

        self.log.info("All configuration have been written 😊")

        # --- apply
        self.write_reg(MomijiReg.CTRL, Ctrl.APPLY)
        self.log.info("APPLY")

        return "Do initializing complete"

    def do_launching(self) -> str:
        """launching, just here

        Returns:
            str: "Do launching complete"
        """

        return "Do launching complete"

    def do_landing(self) -> str:
        """landing, close TCP

        Returns:
            str: _description_
        """
        # --- close TCP
        self._close_tcp()

        return "Do landing complete"

    def do_starting(self, run_identifier: str = None) -> str:
        """starting, connect to TCP, then start triggering

        Args:
            run_identifier (str, optional): from Constellation core. Defaults to None.

        Raises:
            RuntimeError: If error for some reason, close TCP safely

        Returns:
            str: "Do starting complete"
        """

        # --- create TCP socket
        try:
            self.tcp_sock = socket.create_connection((self.cfg["ip_hws"], self.cfg["port_tcp"]), timeout=5)
        except OSError as e:
            raise RuntimeError("Cannot connect TCP. Old connection still open? or the ip/port is wrong") from e

        # --- reset
        self.n_sent = 0
        self._rate_prev = {}
        self._buf = bytearray()

        # --- start trigger + T0 sync
        try:
            self.tcp_sock.settimeout(0.2)
            self.write_reg(MomijiReg.CTRL, Ctrl.RUN_START)
        except Exception as e:
            self._close_tcp()
            raise RuntimeError("TCP/RBCP connection failed") from e

        # --- set begin-of-run tags
        self.bor["firmware_version"] = self.fw_version
        self.bor["eudaq_event"] = "TluRawDataEvent"
        self.bor["write_as_blocks"] = True

        return "Do starting complete"

    def do_run(self) -> str:
        """running until stop requested

        Returns:
            str: "Do running complete"
        """

        # --- receive data and send until stop
        while not self.stop_requested():
            self._receive_and_send()

        return "Do running complete"

    def do_stopping(self) -> str:
        """stopping, safly closing FIFO and socket

        Returns:
            str: "Do stopping complete"
        """

        # --- stop run
        self.write_reg(MomijiReg.CTRL, Ctrl.RUN_STOP)

        # --- finish processing
        t_end = time.time() + 5
        while not (self.read_reg(MomijiReg.STATUS) & Stat.EMPTY) and time.time() < t_end:
            self._receive_and_send()

        while self._receive_and_send():
            pass

        if self._buf:
            self.log.warning(f"{len(self._buf)} bytes left over (not a multiple of {EVT_BYTES})")

        # --- set end-of-run tags
        counters = self._get_counters(time_to_update=0)

        if not counters["n_accept"] == self.n_sent:
            self.log.warning(f"n_accept={counters['n_accept']} != n_sent={self.n_sent}. Check quality")

        self.eor["n_accept"] = counters["n_accept"]
        self.eor["n_drop"] = counters["n_drop"]
        self.eor["n_timeout"] = counters["n_timeout"]
        self.eor["n_in_hit"] = counters["n_in_hit"]

        # --- close TCP socket
        self._close_tcp()

        return "Do stopping complete"

    # =========================================
    # Metrics
    # =========================================
    @schedule_metric("", 2, [SatelliteState.RUN])
    def event_sent(self) -> int:
        return self.n_sent

    @schedule_metric("Hz", 2, [SatelliteState.RUN])
    def trigger_rate(self) -> float | None:
        return self._rate("n_accept")

    @schedule_metric("Hz", 2, [SatelliteState.RUN])
    def drop_rate(self) -> float | None:
        return self._rate("n_drop")

    @schedule_metric("", 2, [SatelliteState.RUN])
    def n_timeout(self) -> float | None:
        return self._get_counters()["n_timeout"]

    def _rate(self, key: str, ch: int | None = None) -> float | None:
        counters = self._get_counters()
        count    = counters[key][ch] if ch is not None else counters[key]
        key_name = f"{key}_{ch}" if ch is not None else key
        prev = self._rate_prev.get(key_name)
        self._rate_prev[key_name] = (count, counters["ts_now"])

        if prev is None or counters["ts_now"] == prev[1]:
            return None
        return (count - prev[0]) / ((counters["ts_now"] - prev[1]) * 6.25e-9)

    # =========================================
    # Utils
    # =========================================
    def write_reg_and_check(self, reg: MomijiReg, value: int, ch: int = 0) -> None:
        """write value to register, and check if the response is same as input

        Args:
            reg (MomijiReg): target register
            value (int): input value
            ch (int, optional): which ch. Defaults to 0.

        Raises:
            RuntimeError: If the check failed
        """

        # --- write to register
        self.write_reg(reg, value, ch)

        # --- see value at shadow register
        response = self.read_reg(reg, ch)

        # --- check
        if response == value:
            self.log.info(f"{reg.name}[{ch}]: wrote 0x{value:02x}")
        else:
            raise RuntimeError(f"{reg.name}: wrote 0x{value:02x}, read 0x{response:02x}")

    def write_reg(self, reg: MomijiReg, value: int, ch: int = 0) -> None:
        """write value to register

        Args:
            reg (MomijiReg): target register
            value (int): input value
            ch (int, optional): which ch. Defaults to 0.
        """
        with self._rbcp_lock:
            self.rbcp.write_uint(reg.addr_at(ch), value, reg.n_bytes)

    def read_reg(self, reg: MomijiReg, ch: int = 0) -> int:
        """read value from register

        Args:
            reg (MomijiReg): target register
            ch (int, optional): which ch. Defaults to 0.

        Returns:
            int: result
        """
        with self._rbcp_lock:
            return self.rbcp.read_uint(reg.addr_at(ch), reg.n_bytes)

    def _read_config(self, config: Configuration) -> dict[str, Any]:
        """read configuration saved in Constellation, and dump it to dict

        Args:
            config (Configuration): original configuration

        Returns:
            dict[str, Any]: dumped cfg
        """

        # --- rewrite polarity setting to 1byte words
        def _to_mask(polarities, width):
            mask = sum(1 << i for i, p in enumerate(polarities) if p == Polarity.FALLING)
            if len(polarities) != width:
                self.log.warning(f"polarities :{polarities}, bit width: {width}, mask: 0x{mask:08x}")
            return mask

        # --- read config
        configuration = {
            "ip_hws": config.get(key="ip_hws", default_value="192.168.10.16"),
            "port_rbcp": config.get_int(key="port_rbcp", default_value=4660),
            "port_tcp": config.get_int(key="port_tcp", default_value=24),
            "dut_mask": config.get_int(key="dut_mask", default_value=0x01),
            "block_mask": config.get_int(key="block_mask", default_value=0x00),
            "trigger_input_logic": config.get_int(key="trigger_input_logic", default_value=0xAAAA),
            "trigger_output_width": config.get_int(
                key="trigger_output_width",
                default_value=16,  # 100ns
            ),
            "trigger_polarity": _to_mask(
                config.get_array(
                    key="trigger_polarity", element_type=enum_type(Polarity), default_value=[Polarity.RISING] * N_CH_IN
                ),
                N_CH_IN,
            ),
            "busy_polarity": _to_mask(
                config.get_array(
                    key="busy_polarity", element_type=enum_type(Polarity), default_value=[Polarity.RISING] * N_CH_BUSY
                ),
                N_CH_BUSY,
            ),
            "trigger_edge_enable": config.get_int(key="trigger_edge_enable", default_value=0x0F),
            "busy_timeout": config.get_int(key="busy_timeout", default_value=0xFFFF),
            "trigger_input_width": config.get_array(
                key="trigger_input_width", element_type=int, default_value=[4] * N_CH_IN
            ),
            "t0_width": config.get_int(
                key="t0_width",
                default_value=16,  # 100ns
            ),
            "busy_input_width": config.get_array(key="busy_input_width", element_type=int, default_value=[1] * N_CH_BUSY),
        }

        # self.status_interval = config.get_float("status_interval")
        # self.log.debug("Calculating status every %.3f s" % self.status_interval)

        return configuration

    def _receive_and_send(self) -> int:
        """receive word from TCP, and send it to receiver satellite

        Raises:
            RuntimeError: TCP connection closed

        Returns:
            int: If all data have read, 0.
        """

        # --- receive data from TCP
        try:
            chunk = self.tcp_sock.recv(65536)
        except socket.timeout:
            return 0
        if not chunk:
            raise RuntimeError("TCP connection closed by the board")

        # --- save to buffer
        self._buf += chunk

        # --- send data per 24 bytes
        evt_in_buf = len(self._buf) // EVT_BYTES
        for k in range(0, evt_in_buf * EVT_BYTES, EVT_BYTES):
            self._send_event(self._buf[k : k + EVT_BYTES])

        del self._buf[: evt_in_buf * EVT_BYTES]

        return len(chunk)

    def _send_event(self, evt: bytearray) -> None:
        """send an event

        Args:
            evt (bytearray): original data from TCP

        Raises:
            RuntimeError: The data is destroyed
        """

        # --- unpack bytearray to 6 word (4 byte per word)
        words = struct.unpack(">6I", evt)

        # --- reconstruct timing info
        timestamp = 25 * ((((words[0] & 0xFFFF) << 32) | words[1]) >> 2)  # [ns]
        trigger_id = words[3]

        # --- check
        if words[5] != 0:
            raise RuntimeError("The data stream crashed 😡!!")

        # --- meta record
        data_record = self.new_data_record(
            {
                "flag_trigger": True,
                "trigger_number": trigger_id,
                "timestamp_begin": int(timestamp * 1000),
                "timestamp_end": int((timestamp + 25) * 1000),
            }
        )
        # --- payload
        data_record.add_block(struct.pack("<6I", *words))

        # ---- send evt data
        self.send_data_record(data_record)
        self.n_sent += 1

    def _close_tcp(self) -> None:
        """safely close TCP"""

        if self.tcp_sock is not None:
            self.tcp_sock.close()
            self.tcp_sock = None

    def _get_counters(self, time_to_update: float = 0.5) -> dict:
        with self._rbcp_lock:
            now = time.monotonic()
            if now - self._counters_time > time_to_update:
                self.write_reg(MomijiReg.CTRL, Ctrl.SNAPSHOT)
                self._counters = {
                    "n_accept": self.read_reg(MomijiReg.N_ACCEPT),
                    "n_drop": self.read_reg(MomijiReg.N_DROP),
                    "n_timeout": self.read_reg(MomijiReg.N_TIMEOUT),
                    "n_in_hit": [self.read_reg(MomijiReg.N_IN_HIT, ch=i) for i in range(N_CH_IN)],
                    "ts_now": self.read_reg(MomijiReg.TS_NOW),
                }

                self._counters_time = now
            return self._counters
