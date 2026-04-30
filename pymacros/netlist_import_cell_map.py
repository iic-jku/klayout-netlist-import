# --------------------------------------------------------------------------------
# SPDX-FileCopyrightText: 2026 Martin Jan Köhler
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
# SPDX-License-Identifier: GPL-3.0-or-later
#--------------------------------------------------------------------------------

from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, asdict, field
from datetime import datetime
from functools import cached_property
import json
import os 
from pathlib import Path
import re
import sys
import threading
import traceback
from typing import *

import pya

from klayout_plugin_utils.str_enum_compat import DualStrEnum


class CellType(DualStrEnum):
    STATIC_CELL = 'static_cell', 'Static Cell'
    PCELL = 'pcell', 'PCell'


@dataclass
class ParameterMapping:
    entries: Dict[str, str] = field(default_factory=dict)


@dataclass 
class CellMapEntry:
    netlist_device: str
    layout_cell_library: str
    layout_cell: str
    layout_cell_type: CellType
    parameter_mapping: ParameterMapping = field(default_factory=ParameterMapping)

    
@dataclass
class CellMap:
    entries: List[CellMapEntry] = field(default_factory=list)

    def map_entry_for_device(self, netlist_device: str) -> Optional[CellMapEntry]:
        for e in self.entries:
            if e.netlist_device.lower() == netlist_device.lower():
                return e
        return None
