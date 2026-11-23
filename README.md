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

## 📝 Requirements
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
uv sync
```
If you need constellation cli,
```shell
uv sync --extra cli
```
If you are a developer, to install ruff and basedpyright
```shell
uv sync --extra dev
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
| `trigger_logic` | Trigger logic for 4 ch input | Str | - |
| `t0_width` | T0 signal width for Caribou | Int | `16` (100ns)|
| `ip_hws` | IP address of HwS | Str | `192.168.10.16` |
| `port_rbcp` | Port of RBCP connection | Int | `4660` |
| `port_tcp` | Port of TCP connection | Int | `24` |
| `dt_trigger` | Dead time after accepted trigger | Int | `0` [6.25ns]|
| `trigger_output_width` | Output length of trigger signal | Int | `16` (100 ns)|
| `busy_timeout` | Time to wait busy response | Int | `0xFFFF` |
| `mode` | Select input mode for trigger/busy | Enum(mode) | `edge`(trigger), `off`(busy) |
| `width` | Fixed trigger/busy length pulsed to original trigger/busy edge | list[Int] | `4`(trigger), `1`(busy) |
| `polarity` | Which polarity do the trigger/busy inputs have | list[Enum] | `["rising"]*4` |
| `threshold` | TODO | list[float]? | TODO |

### Mode

- Trigger input mode:
  - `EDGE`: enable trigger input edge
  - `LEVEL`: receive triggger original input as gate
- Busy input mode:
  - `HANDSHAKE`: use ch as DUT for handshake
  - `VETO`: use ch as VETO signal
  - `OFF`: not concern

### Configuration Example

An example configuration for this satellite which could be dropped into a Constellation configuration as a starting point

```toml
[MomijiTLU.tlu]
ip_hws = "192.168.10.16"
trigger_logic = "CH0 and not CH1"
trigger_output_width = 255

[MomijiTLU.tlu.busy0]
mode = "handshake"

[MomijiTLU.tlu.busy1]
mode = "veto"

[MomijiTLU.tlu.trigger0]
width = 10
polarity = "rising"
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

