"""
SPDX-FileCopyrightText: 2025 DESY and the Constellation authors
SPDX-License-Identifier: EUPL-1.2
"""

import ast
import logging
import socket
import struct
import threading
import time

from . import rbcp
from .registers import EVT_BYTES, N_CH_BUSY, N_CH_IN, Ctrl, MomijiReg, Stat

DEFAULT_IP = rbcp.DEFAULT_IP
DEFAULT_PORT = rbcp.DEFAULT_PORT

class TriggerBoard:
    """Momiji TLU hardware: RBCP registers (UDP) and event data (TCP)

    Knows nothing about Constellation. The satellite reads the configuration,
    and calls these methods from its state machine.
    """

    def __init__(self, conf: dict, log: logging.Logger | None = None) -> None:
        """open the RBCP socket (no packet is sent yet)

        Args:
            conf (dict): configuration, needs "ip_hws", "port_rbcp", "port_tcp"
            log (logging.Logger, optional): logger of the satellite
        """
        self.ip = conf["ip_hws"]
        self.port_tcp = conf["port_tcp"]
        self.log = log or logging.getLogger(__name__)
        self.fw_version = None

        self._rbcp = rbcp.Rbcp(ip=self.ip, port=conf["port_rbcp"])
        self._rbcp_lock = threading.RLock()
        self._tcp_sock = None
        self._buf = bytearray()
        self._counters = {}
        self._counters_time = 0

    def close(self) -> None:
        """close RBCP and TCP sockets"""
        self.close_tcp()
        self._rbcp.close()

    def status(self) -> Stat:
        """get status"""
        return Stat(self.read_reg(MomijiReg.STATUS))

    # =========================================
    # Initializing
    # =========================================
    def check_firmware(self) -> int:
        """read FW_VERSION / N_IN / N_BUSY and check the channel numbers

        Raises:
            RuntimeError: N_IN or N_BUSY is not what the satellite expects

        Returns:
            int: firmware version
        """
        self.fw_version = self.read_reg(MomijiReg.FW_VERSION)
        n_in = self.read_reg(MomijiReg.N_IN)
        n_busy = self.read_reg(MomijiReg.N_BUSY)

        if (n_in, n_busy) != (N_CH_IN, N_CH_BUSY):
            raise RuntimeError(f"FW has N_IN={n_in}, N_BUSY={n_busy}, satellite expects {N_CH_IN}/{N_CH_BUSY}")

        self.log.info(f"FW_VERSION 0x{self.fw_version:08x}")
        self.log.info(f"N_IN       {n_in}")
        self.log.info(f"N_BUSY     {n_busy}")
        self.log.info("ID check PASSED 😊")

        if Stat.LOCK not in self.status():
            raise RuntimeError("PLL not locked")
        
        return self.fw_version

    def configure(self, conf: dict) -> None:
        """write all settings to the shadow registers, check them, then APPLY

        Args:
            conf (dict): configuration made by the satellite
        """
        self.write_reg_and_check(MomijiReg.DUT_MASK, conf["dut_mask"])
        self.write_reg_and_check(MomijiReg.BLOCK_MASK, conf["block_mask"])
        self.write_reg_and_check(MomijiReg.LUT, conf["trigger_logic"])
        self.write_reg_and_check(MomijiReg.TRG_O_WIDTH, conf["trigger_output_width"])
        self.write_reg_and_check(MomijiReg.TRG_INVERT, conf["trigger_polarity"])
        self.write_reg_and_check(MomijiReg.BUSY_INVERT, conf["busy_polarity"])
        self.write_reg_and_check(MomijiReg.TRG_EDGE_EN, conf["trigger_edge_enable"])
        self.write_reg_and_check(MomijiReg.BUSY_TIMEOUT, conf["busy_timeout"])
        self.write_reg_and_check(MomijiReg.DT_TRG, conf["dt_trigger"])
        self.write_reg_and_check(MomijiReg.T0_WIDTH, conf["t0_width"])

        for i in range(N_CH_IN):
            self.write_reg_and_check(MomijiReg.TRG_I_WIDTH, conf["trigger_input_width"][i], ch=i)
        for i in range(N_CH_BUSY):
            self.write_reg_and_check(MomijiReg.BUSY_WIDTH, conf["busy_input_width"][i], ch=i)

        self.log.info("All configuration have been written 😊")

        self.write_reg(MomijiReg.CTRL, Ctrl.APPLY)
        self.log.info("APPLY")

    # =========================================
    # Run control
    # =========================================
    def start_run(self) -> None:
        """connect TCP, then RUN_START (trigger on + T0)

        Raises:
            RuntimeError: TCP cannot connect, or RUN_START failed (TCP is closed again)
        """
        try:
            self._tcp_sock = socket.create_connection((self.ip, self.port_tcp), timeout=5)
        except OSError as e:
            raise RuntimeError("Cannot connect TCP. Old connection still open? or the ip/port is wrong") from e

        self._buf = bytearray()

        try:
            self._tcp_sock.settimeout(0.2)
            self.write_reg(MomijiReg.CTRL, Ctrl.RUN_START)
        except Exception as e:
            self.close_tcp()
            raise RuntimeError("TCP/RBCP connection failed") from e

    def stop_run(self) -> None:
        """RUN_STOP: no more triggers, the FIFO is still sent out"""
        self.write_reg(MomijiReg.CTRL, Ctrl.RUN_STOP)

    def is_empty(self) -> bool:
        """True when the FIFO and the TCP sender of the board are empty"""
        return bool(self.read_reg(MomijiReg.STATUS) & Stat.EMPTY)

    def read_events(self) -> list[bytes] | None:
        """receive from TCP and cut the stream into 24-byte events

        Raises:
            RuntimeError: TCP connection closed by the board

        Returns:
            list[bytes] | None: complete events (can be empty), None if nothing arrived (timeout)
        """
        try:
            chunk = self._tcp_sock.recv(65536)
        except TimeoutError:
            return None
        if not chunk:
            raise RuntimeError("TCP connection closed by the board")

        self._buf += chunk
        n_bytes = len(self._buf) // EVT_BYTES * EVT_BYTES
        events = [bytes(self._buf[k : k + EVT_BYTES]) for k in range(0, n_bytes, EVT_BYTES)]
        del self._buf[:n_bytes]
        return events

    @property
    def leftover_bytes(self) -> int:
        """bytes received but not yet a complete event"""
        return len(self._buf)

    def close_tcp(self) -> None:
        """safely close TCP"""
        if self._tcp_sock is not None:
            self._tcp_sock.close()
            self._tcp_sock = None

    # =========================================
    # Counters
    # =========================================
    def counters(self, time_to_update: float = 0.5) -> dict:
        """SNAPSHOT and read the counters, at most once per time_to_update [s]

        Args:
            time_to_update (float, optional): reuse the last values if they are newer than this

        Returns:
            dict: n_accept, n_drop, n_timeout, n_in_hit (list), ts_now
        """
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

    # =========================================
    # Registers
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
        self.write_reg(reg, value, ch)
        response = self.read_reg(reg, ch)  # value in the shadow register

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
            self._rbcp.write_uint(reg.addr_at(ch), value, reg.n_bytes)

    def read_reg(self, reg: MomijiReg, ch: int = 0) -> int:
        """read value from register

        Args:
            reg (MomijiReg): target register
            ch (int, optional): which ch. Defaults to 0.

        Returns:
            int: result
        """
        with self._rbcp_lock:
            return self._rbcp.read_uint(reg.addr_at(ch), reg.n_bytes)


def decode_event(evt: bytes) -> tuple[int, int, tuple[int, ...]]:
    """unpack one 24-byte event (6 words, MSB first)

    Args:
        evt (bytes): one event from TCP

    Raises:
        RuntimeError: the last word is not 0 (stream out of step)

    Returns:
        tuple: trigger ID, timestamp [ns] in 25 ns steps (40 MHz), the 6 words
    """
    words = struct.unpack(">6I", evt)
    if words[5] != 0:
        raise RuntimeError("The data stream crashed 😡!!")

    timestamp = 25 * ((((words[0] & 0xFFFF) << 32) | words[1]) >> 2)  # [ns]
    trigger_id = words[3]
    return trigger_id, timestamp, words


def logic_to_lut(expr: str, n_in: int = N_CH_IN) -> int:
    """'CH1 and not CH4' -> LUT. bit i = result for inputs = bits of i (CH1 = bit0)"""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"cannot parse {expr!r}") from e
    names = {f"CH{k}" for k in range(n_in)}

    def ev(node, gates):
        match node:
            case ast.Expression(body=b):
                return ev(b, gates)
            case ast.BoolOp(op=ast.And(), values=vs):
                return all(ev(v, gates) for v in vs)
            case ast.BoolOp(op=ast.Or(), values=vs):
                return any(ev(v, gates) for v in vs)
            case ast.UnaryOp(op=ast.Not(), operand=o):
                return not ev(o, gates)
            case ast.Name(id=name) if name.upper() in names:
                return gates[int(name[2:]) - 1]
        raise ValueError(f"not allowed: {ast.unparse(node)!r}")

    lut = 0
    for i in range(1, 2**n_in):  # i = 0 (no input) is not used by the FW
        gates = [bool(i >> k & 1) for k in range(n_in)]
        if ev(tree, gates):
            lut |= 1 << i
    if lut == 0:
        raise ValueError(f"{expr!r} never fires")
    return lut