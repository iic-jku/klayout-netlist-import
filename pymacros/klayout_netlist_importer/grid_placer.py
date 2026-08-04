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
from dataclasses import dataclass
from typing import *

import pya

from klayout_plugin_utils.debugging import debug, Debugging

from klayout_netlist_importer.netlist_import_config import NetlistImportConfig


@dataclass
class GridPosition:
    x: float
    y: float


class GridPlacer:
    """Places cells on a grid, wrapping columns per config.
    
    Two modes:
      - Fixed pitch (avoid_overlaps=False): original fast/uniform behavior.
      - Packed (avoid_overlaps=True): positions are derived from each cell's
        actual bounding box, so instances never overlap regardless of size.
    """
    
    def __init__(self, config: NetlistImportConfig):
        self.origin_x = config.layout_config.origin_x
        self.origin_y = config.layout_config.origin_y
        self.pitch = config.layout_config.pitch  # for fixed mode
        self.padding = config.layout_config.padding  # for packed mod
        self.limit_columns = config.layout_config.limit_columns
        self.max_columns = config.layout_config.max_columns
        self.avoid_overlaps = config.layout_config.avoid_overlaps
                
        self.reset()
    
    def reset(self):
        self.col = 0
        self.row = 0
        self._x_cursor = self.origin_x
        self._y_cursor = self.origin_y
        self._row_height = 0.0
    
    def next_position(self, cell: Optional[pya.Cell] = None) -> GridPosition:
        """Return the position for the next instance.

        In packed mode, *cell* must be the already-resolved master cell
        (post pcell-parameter-application) so its real bbox can be used.
        """
        if self.avoid_overlaps:
            if cell is None:
                raise ValueError("GridPlacer.next_position: 'cell' is required when avoid_overlaps is enabled")
            return self._next_position_packed(cell)
        return self._next_position_fixed()

    def _next_position_fixed(self) -> GridPosition:
        x = self.origin_x + self.col * self.pitch
        y = self.origin_y + self.row * self.pitch
        
        self.col += 1
        if self.limit_columns and self.col >= self.max_columns:
            self.col = 0
            self.row += 1
        
        return GridPosition(x, y)

    def _next_position_packed(self, cell: pya.Cell) -> GridPosition:
        bbox = cell.dbbox()

        if bbox.empty():
            width = height = 0.0
            left = bottom = 0.0
        else:
            width = bbox.width()
            height = bbox.height()
            left = bbox.left
            bottom = bbox.bottom

        if self.limit_columns and self.col >= self.max_columns:
            # wrap row
            self.col = 0
            self.row += 1
            self._x_cursor = self.origin_x
            self._y_cursor += self._row_height + self.padding
            self._row_height = 0.0
        
        # Shift so the cell's own bbox-left/bottom lands exactly at the cursor,
        # regardless of where the cell's shapes sit relative to its origin.
        x = self._x_cursor - left
        y = self._y_cursor - bottom

        self._x_cursor += width + self.padding
        self._row_height = max(self._row_height, height)
        self.col += 1

        return GridPosition(x, y)

