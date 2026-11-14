---
# SPDX-FileCopyrightText: 2025 DESY and the Constellation authors
# SPDX-License-Identifier: CC-BY-4.0 OR EUPL-1.2
title: "MomijiTLU"
description: "Satellite for the Momiji TLU using a Python-based control software"
category: "Readout systems"
language: "Python"
parent_class: "Satellite"
version: v0.1.0
---

## 📌 Description

This is a detailed description of the satellite and its functionality.
Possible dependencies are described alongside its features, potential pitfalls and other information.

## Requirements
- Python >= 3.11 (tested: Python3.12, ConstellationDAQ 1.0.2
- uv (or plain `python -m venv`+`pip`)
- Momiji TLU board reachable over Ethernet
- PC NIC on the same subnet, no gateway. Do not connect the board to a public network

## 🏢 Building
Create new virtual environment
```shell
git clone git clone git@github.com:NipponSword/momiji-tlu-satellite.git
cd momiji-tlu-satellite
uv venv --python 3.12
source .venv/bin/activate
```
Then, install satellite
```shell
uv pip install -e .
```
If you need constellation cli,
```shell
uv pip install "ConstellationDAQ[cli]"
```
If you are a developer, to install ruff and basedpyright
```shell
uv pip install -e ".[dev]"
```

## 🚀 Usage
Before starting satellite, check TCP connection
```shell
ping <IP address of the board>
```
Finally, start satellite with 
```shell
SatelliteMomijiTLU -n <name of tlu> -g <group name>
```
Note: The trigger board must start run after all other detectors.

## ✅ Parameters

The following parameters are read and interpreted by this satellite. Parameters without a default value are required.

| Parameter | Description | Type | Default Value |
| --------- | ----------- | ---- | ------------- |
| `ip_hws` | IP address of HwS | Str | `192.168.10.16` |
| `port_rbcp` | Port of RBCP connection | Int | `4660` |
| `port_tcp` | Port of TCP connection | Int | `24` |
| `dut_mask` | Select busy ch from DUT (used for handshake) | Int | `0x01` |
| `block_mask` | Select busy ch for VETO  | Int | `0x00` |
| `trigger_input_logic` | Trigger logic for 4 ch input (LUT) | Int | `0xAAAA` |
| `trigger_output_width` | Output length of trigger signal | Int | `16` (100 ns)|
| `trigger_polarity` | Which polarity do the trigger inputs have | list[Enum] | `["rising"]*4` |
| `busy_polarity` | Which polarity do the busy inputs have | list[Enum] | `["rising"]*5` |
| `trigger_edge_enable` | Enable edge detection of trigger (level or edge) | Int | `0x0F` |
| `busy_timeout` | Time to wait busy response | Int | `0xFFFF` |
| `trigger_input_width` | How long the input signal stretched. Higher value, more accidental coincidence | list[Int] | `[4,4,4,4]` (25ns) |
| `t0_width` | T0 signal width for Caribou | Int | `16` (100ns)|
| `busy_input_width` | Fixed busy length pulsed to original busy  | list[Int] | `[1,1,1,1,1]` |
| `trigger_threshold` | TODO | list[float]? | TODO |

### Configuration Example

An example configuration for this satellite which could be dropped into a Constellation configuration as a starting point

```toml
[MomijiTLU.tlu]
dut_mask = 4b0001
block_mask = 5b00100
trigger_input_logic = 0xAAAA
busy_input_width = [0, 0, 0, 0, 0]
```

## 📉 Metrics

The following metrics are distributed by this satellite and can be subscribed to.

| Metric | Description | Value Type | Interval |
| ------ | ----------- | ---------- | -------- |
| `input_rate_{ch}` | Trigger input rate for each channel | Float | 2s |
| `event_sent` | Number of events sent through TCP | Int | 2s |
| `trigger_rate` | Temporal accepted trigger rate | Float | 2s |
| `drop_rate` | Rate of droped trigger due to busy | Float | 2s |
| `n_timeout` | Number of handshake timeout. If not 0 the events cannot be reconstructed | Int | 2s |

