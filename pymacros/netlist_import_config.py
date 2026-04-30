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


CONFIG_KEY__NETLIST_IMPORT_CONFIG = 'klayout_netlist_import_config'


#
# NOTE: as this file is used from klayout-vector-file-export-cli,
#       no dependency on pya is allowed!
#
from klayout_plugin_utils.str_enum_compat import StrEnum, DualStrEnum


#--------------------------------

class NetlistFileFormat(DualStrEnum):
    KLAYOUT_LVS_NETLIST = 'lvs_cdl_netlist', 'SPICE Netlist (KLayout LVS format)'
    SPICE_SIMULATION_NETLIST = 'simulation_spice_netlist', 'SPICE Netlist (Simulation format)'

    @property
    def suffix(self) -> str:
        if self == NetlistFileFormat.KLAYOUT_LVS_NETLIST:
            return f'.cdl'
        elif self == NetlistFileFormat.SPICE_SIMULATION_NETLIST:
            return f'.cir'
        else:
            raise NotImplementedError(f"NetlistFileFormat.suffix: unhandled case {self}")


class HierarchyMode(DualStrEnum):
    PRESERVE_HIERARCHY = 'preserve_hierarchy', 'Preserve Hierarchy'
    FLATTEN = 'flatten_hierarchy', 'Flatten Hierarchy'


class CellType(DualStrEnum):
    STATIC_CELL = 'static_cell', 'Static Cell'
    PCELL = 'pcell', 'PCell'


@dataclass
class ParameterMapping:
    entries: Dict[str, str] = field(default_factory=dict)


@dataclass 
class CellMapEntry:
    netlist_device: str
    target: str
    target_type: CellType
    parameter_mapping: ParameterMapping = field(default_factory=ParameterMapping)

    
@dataclass
class CellMap:
    entries: List[CellMapEntry] = field(default_factory=list)


@dataclass
class NetlistImportConfig:
    source_path: Optional[Path] = None
    file_format: NetlistFileFormat = NetlistFileFormat.SPICE_SIMULATION_NETLIST
    hierarchy_mode: HierarchyMode = HierarchyMode.PRESERVE_HIERARCHY
    cell_map: CellMap = field(default_factory=CellMap)

    @classmethod
    def load_json(cls, json_path: Path) -> NetlistImportConfig:
        text = json_path.read_text(encoding='utf-8')
        data = json.loads(text)
        settings = NetlistImportConfig.from_dict(data)
        return settings
    
    def save_json(self, json_path: Path):
        data = self.dict()
        json_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    
    @classmethod
    def from_dict(cls, d: Dict) -> NetlistImportConfig:
        settings = NetlistImportConfig()        
    
        file_format_str = d.get('file_format', None)
        if file_format_str is not None:
            settings.file_format = NetlistFileFormat(file_format_str)
        
        source_path_str = d.get('source_path', None)
        if source_path_str is not None:
            settings.source_path = Path(source_path_str)
            
        hierarchy_mode_str = d.get('hierarchy_mode', None)
        if hierarchy_mode_str is not None:
            settings.hierarchy_mode = HierarchyMode(hierarchy_mode_str)
        
        cell_map_data = d.get('cell_map', None)
        if cell_map_data is not None and isinstance(cell_map_data, dict):
            entries = [
                CellMapEntry(
                    netlist_device = e['netlist_device'],
                    target         = e['target'],
                    target_type    = CellType(e['target_type']),
                    parameter_mapping = ParameterMapping(entries=e.get('parameter_mapping', {}).get('entries', {}))
                )
                for e in cell_map_data.get('entries', [])
            ]
            settings.cell_map = CellMap(entries=entries)        

        return settings
    
    def dict(self) -> Dict:
        return {
            'source_path': str(self.source_path),
            'file_format': self.file_format.value,
            'hierarchy_mode': self.hierarchy_mode,
            'cell_map': {
                'entries': [
                    {
                        'netlist_device': e.netlist_device,
                        'target': e.target,
                        'target_type': e.target_type.value,
                        'parameter_mapping': {'entries': e.parameter_mapping.entries}
                    }
                    for e in self.cell_map.entries
                ]
            }
        }
    
