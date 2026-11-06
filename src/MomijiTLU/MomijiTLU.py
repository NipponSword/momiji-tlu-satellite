"""
SPDX-FileCopyrightText: 2025 DESY and the Constellation authors
SPDX-License-Identifier: EUPL-1.2
"""

from constellation.core.transmitter_satellite import TransmitterSatellite

from . import __version__


class MomijiTLU(Satellite, version=__version__):
    pass
