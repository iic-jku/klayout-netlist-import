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
from typing import *

import pya

from klayout_plugin_utils.debugging import debug, Debugging

from netlist_import_config import NetlistImportConfig


@dataclass
class GridPosition:
    x: float
    y: float


class GridPlacer:
    """Places cells on a grid, wrapping columns per config."""
    
    def __init__(self, config: NetlistImportConfig):
        self.origin_x = config.origin_x
        self.origin_y = config.origin_y
        self.pitch = config.pitch
        self.limit_columns = config.limit_columns
        self.max_columns = config.max_columns
        self.col = 0
        self.row = 0
    
    def reset(self):
        self.col = 0
        self.row = 0
    
    def next_position(self) -> GridPosition:
        x = self.origin_x + self.col * self.pitch
        y = self.origin_y + self.row * self.pitch
        
        self.col += 1
        if self.limit_columns and self.col >= self.max_columns:
            self.col = 0
            self.row += 1
        
        return GridPosition(x, y)