"""
SPDX-FileCopyrightText: 2025 DESY and the Constellation authors
SPDX-License-Identifier: EUPL-1.2
"""

import struct
import time
from enum import StrEnum
from pathlib import Path
from typing import Any

from constellation.core.configuration import Configuration, Section
from constellation.core.monitoring import schedule_metric
from constellation.core.protocol.cscp1 import SatelliteState
from constellation.core.transmitter_satellite import TransmitterSatellite

from . import __version__
from .registers import EVT_BYTES, N_CH_BUSY, N_CH_IN, Stat
from .trigger_board import DEFAULT_IP, DEFAULT_PORT, TriggerBoard, decode_event, logic_to_lut

class TriggerInMode(StrEnum):
    """Trigger input mode definition"""

    EDGE = "edge"
    LEVEL = "level"

class Polarity(StrEnum):
    """Polarity definition"""

    RISING = "rising"
    FALLING = "falling"

class BusyInMode(StrEnum):
    """Busy input mode definition"""

    HANDSHAKE = "handshake"
    VETO = "veto"
    OFF = "off"

def bits_where(values: list, target) -> int:
    """bit k = 1 where values[k] == target (values from Constellation are plain str)"""
    return sum(1 << k for k, v in enumerate(values) if v == target)

class MomijiTLU(TransmitterSatellite, version=__version__):
    """Momiji-TLU satellite

    Main class of Momiji TLU, enabling to control the TLU, receive data, and send data.
    The hardware access is in TriggerBoard; this class only runs the Constellation FSM.

    Attributes:
        board (TriggerBoard): the trigger board
        n_sent (int): the number of data sent to receiver satellite
        cfg (dict): dict derived from constellation.core.configuration
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # --- members
        self.file_path = Path(__file__).parent
        self.board: TriggerBoard | None = None
        self.n_sent = 0
        self.n_id_gap = 0
        self.cfg = None

        # --- secret
        self._next_id = 0
        self._rate_prev = {}
        self._count_prev = {}

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

        if self.board:
            self.board.close()
        self.board = TriggerBoard(self.cfg, log=self.log)

        # --- check firmware settings, write configuration to RBCP regs, APPLY
        self.board.check_firmware()
        self.board.configure(self.cfg)

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
            str: "Do landing complete"
        """
        self.board.close_tcp()

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

        # --- reset
        self.n_sent = 0
        self.n_id_gap = 0
        self._next_id = 0
        self._rate_prev = {}
        self._count_prev = {}

        # --- connect TCP, start trigger + T0 sync
        self.board.start_run()

        # --- set begin-of-run tags
        self.bor["firmware_version"] = self.board.fw_version
        self.bor["eudaq_event"] = "TluRawDataEvent"
        self.bor["write_as_blocks"] = True
        self.bor.update({k: v for k, v in self.cfg.items() 
                         if k not in ("ip_hws", "port_rbcp", "port_tcp")}
                        )

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
        self.board.stop_run()

        # --- finish processing
        t_end = time.time() + 5
        while not self.board.is_empty() and time.time() < t_end:
            self._receive_and_send()

        while self._receive_and_send():
            pass

        if self.board.leftover_bytes:
            self.log.warning(f"{self.board.leftover_bytes} bytes left over (not a multiple of {EVT_BYTES})")

        # --- set end-of-run tags
        counters = self.board.counters(time_to_update=0)

        if not counters["n_accept"] == self.n_sent:
            self.log.warning(f"n_accept={counters['n_accept']} != n_sent={self.n_sent}. Check quality")

        if counters["n_timeout"]:
            self.log.error(f"n_timeout={counters['n_timeout']}: a DUT did not answer busy")

        run_length_s = counters["ts_now"] * 6.25e-9          # TS はラン開始で 0
        n_trig = counters["n_accept"] + counters["n_drop"]
        
        self.eor["n_accept"] = counters["n_accept"]
        self.eor["n_drop"] = counters["n_drop"]
        self.eor["n_timeout"] = counters["n_timeout"]
        self.eor["n_in_hit"] = counters["n_in_hit"]
        self.eor["n_id_gap"] = self.n_id_gap
        self.eor["run_length_s"] = run_length_s
        self.eor["drop_fraction"] = counters["n_drop"] / n_trig if n_trig else 0.0

        # --- close TCP socket
        self.board.close_tcp()

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
    def almost_full(self) -> int:
        af = Stat.ALMOST_FULL in self.board.status()
        if af:
            self.log.warning("FIFO almost full: triggers are inhibited")
        return int(af)

    @schedule_metric("", 2, [SatelliteState.RUN])
    def n_timeout(self) -> float | None:
        n_timeout = self.board.counters()["n_timeout"]
        prev = self._count_prev.get("n_timeout")
        if not (prev is None or n_timeout == prev):
            self.log.error(f"busy timeout ! n_timeout = {n_timeout}")
        self._count_prev["n_timeout"] = n_timeout
        return n_timeout 

    def _rate(self, key: str, ch: int | None = None) -> float | None:
        counters = self.board.counters()
        count = counters[key][ch] if ch is not None else counters[key]
        key_name = f"{key}_{ch}" if ch is not None else key
        prev = self._rate_prev.get(key_name)
        self._rate_prev[key_name] = (count, counters["ts_now"])

        if prev is None or counters["ts_now"] == prev[1]:
            return None
        return (count - prev[0]) / ((counters["ts_now"] - prev[1]) * 6.25e-9)

    # =========================================
    # Utils
    # =========================================
    def _read_config(self, config: Configuration) -> dict[str, Any]:
        """read configuration saved in Constellation, and dump it to dict

        Args:
            config (Configuration): original configuration

        Returns:
            dict[str, Any]: dumped cfg
        """

        # --- default config
        config.set_default(key="ip_hws", value=DEFAULT_IP)
        config.set_default(key="port_rbcp", value=DEFAULT_PORT)
        config.set_default(key="port_tcp", value=24)
        config.set_default(key="t0_width", value=16)
        config.set_default(key="trigger_output_width", value=16)
        config.set_default(key="busy_timeout", value=0xFFFF)
        config.set_default(key="dt_trigger", value=0)

        # --- read config
        configuration = {
            "ip_hws": config.get(key="ip_hws"),
            "port_rbcp": config.get_int(key="port_rbcp"),
            "port_tcp": config.get_int(key="port_tcp"),
            "trigger_logic_expr": config.get_str(key="trigger_logic"),
            "trigger_logic": config.get(key="trigger_logic", return_type=logic_to_lut),
            "t0_width": config.get_int(key="t0_width"),
            "trigger_output_width": config.get_int(key="trigger_output_width"),
            "busy_timeout": config.get_int(key="busy_timeout"),
            "dt_trigger": config.get_int(key="dt_trigger", max_val=0xFFFF),
        }

        # --- trigger section
        pol, mode, width = [], [], []
        for ch in range(N_CH_IN):
            sec = self._get_section(config, f"trigger{ch}")
            mode.append(sec.get_enum(TriggerInMode, "mode", TriggerInMode.EDGE))
            pol.append(sec.get_enum(Polarity, "polarity", Polarity.RISING))
            width.append(sec.get_int("width", 4, min_val=0, max_val=255))

        configuration["trigger_edge_enable"] = bits_where(mode, TriggerInMode.EDGE)
        configuration["trigger_polarity"] = bits_where(pol, Polarity.FALLING)
        configuration["trigger_input_width"] = width

        # --- busy section
        pol, mode, width = [], [], []
        for ch in range(N_CH_BUSY):
            sec = self._get_section(config, f"busy{ch}")
            mode.append(sec.get_enum(BusyInMode, "mode", BusyInMode.OFF))
            pol.append(sec.get_enum(Polarity, "polarity", Polarity.RISING))
            width.append(sec.get_int("width", 1, min_val=0, max_val=255))

        configuration["dut_mask"] = bits_where(mode, BusyInMode.HANDSHAKE)
        configuration["block_mask"] = bits_where(mode, BusyInMode.VETO)
        configuration["busy_polarity"] = bits_where(pol, Polarity.FALLING)
        configuration["busy_input_width"] = width

        return configuration

    def _receive_and_send(self) -> bool:
        """receive from the board, and send every complete event to the receiver satellite

        Returns:
            bool: False if nothing arrived (timeout)
        """
        events = self.board.read_events()
        if events is None:
            return False

        for evt in events:
            self._send_event(evt)
        return True

    def _send_event(self, evt: bytes) -> None:
        """send an event

        Args:
            evt (bytes): one 24-byte event from the board
        """
        trigger_id, timestamp, words = decode_event(evt)

        # --- check id is consistent as expected
        if trigger_id - self._next_id != 0:
            self.n_id_gap += trigger_id - self._next_id
            self.log.warning(f"trigger ID {trigger_id}, expected {self._next_id}")
        self._next_id = trigger_id + 1

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

    @staticmethod
    def _get_section(config:Configuration, key: str) -> Section:
        return config.get_section(key) if config.has(key) else Section(f"{key}.", {})
