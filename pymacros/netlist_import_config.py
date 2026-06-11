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


from netlist_import_cell_map import *
from netlist_pdk_info import NetlistPDKInfo, NetlistPDKInfoFactory
from klayout_plugin_utils.str_enum_compat import StrEnum, DualStrEnum


CONFIG_KEY__NETLIST_IMPORT_CONFIG = 'klayout_netlist_import_config'


#--------------------------------

class NetlistFileFormat(DualStrEnum):
    # KLAYOUT_LVS_NETLIST = 'lvs_cdl_netlist', 'SPICE Netlist (KLayout LVS format)'
    SPICE_SIMULATION_NETLIST = 'simulation_spice_netlist', 'SPICE Netlist (Simulation format)'


class HierarchyMode(DualStrEnum):
    PRESERVE_HIERARCHY = 'preserve_hierarchy', 'Preserve Hierarchy'
    # FLATTEN = 'flatten_hierarchy', 'Flatten Hierarchy'


class ImportMode(DualStrEnum):
    IGNORE = 'ignore', 'Ignore'
    NEW_CELL = 'new_cell', 'New Cell'
    NETLIST_CELL = 'netlist_cell', 'Netlist Cell'
    TECH_CELL_MAPPING = 'tech_cell_mapping', 'Tech Cell Mapping'
    EXTERNAL_STATIC_CELL = 'external_static_cell', 'External Static Cell'


@dataclass
class InstanceImportSetting:
    """Per-instance import setting (persisted)."""
    instance_name: str = ''
    device_name: str = ''
    import_mode: ImportMode = ImportMode.TECH_CELL_MAPPING
    static_library: str = ''
    static_cell: str = ''


@dataclass
class CellImportSetting:
    """Per-cell import setting with nested instance settings (persisted)."""
    cell_name: str = ''
    import_mode: ImportMode = ImportMode.NEW_CELL
    static_library: str = ''
    static_cell: str = ''
    instance_settings: List[InstanceImportSetting] = field(default_factory=list)

    def instance_setting_for(self, instance_name: str) -> Optional[InstanceImportSetting]:
        for s in self.instance_settings:
            if s.instance_name == instance_name:
                return s
        return None


@dataclass
class NetlistImportConfig:
    source_path: Optional[Path] = None
    file_format: NetlistFileFormat = NetlistFileFormat.SPICE_SIMULATION_NETLIST
    hierarchy_mode: HierarchyMode = HierarchyMode.PRESERVE_HIERARCHY
    cell_map: CellMap = field(default_factory=CellMap)
    cell_import_settings: List[CellImportSetting] = field(default_factory=list)    
    origin_x: float = 0.0
    origin_y: float = 0.0
    limit_columns: bool = True
    max_columns: int = 10   
    pitch: float = 50.0    # µm
    
    @classmethod
    def default_for_tech(cls, tech: pya.Technology) -> NetlistImportConfig:
        script_dir = Path(__file__).resolve().parent
        pdk_info_factory = NetlistPDKInfoFactory(search_path=[script_dir / '..' / 'pdks'])
        netlist_pdk_info = pdk_info_factory.pdk_info(tech.name)
        config = NetlistImportConfig()
        if netlist_pdk_info is not None:
            config.cell_map = netlist_pdk_info.cell_map
        return config

    def cell_import_setting_for(self, cell_name: str) -> Optional[CellImportSetting]:
        for s in self.cell_import_settings:
            if s.cell_name == cell_name:
                return s
        return None

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
        if cell_map_data is not None:
            if isinstance(cell_map_data, str):
                try:
                    import ast
                    cell_map_data = ast.literal_eval(cell_map_data)
                except Exception:
                    cell_map_data = None
            if isinstance(cell_map_data, dict):
                entries = [
                    CellMapEntry(
                        netlist_device = e['netlist_device'],
                        layout_cell_library = e['layout_cell_library'],
                        layout_cell = e['layout_cell'],
                        layout_cell_type = CellType(e['layout_cell_type']),
                        parameter_mapping = ParameterMapping(entries=e.get('parameter_mapping', {}).get('entries', {}))
                    )
                    for e in cell_map_data.get('entries', [])
                ]
                settings.cell_map = CellMap(entries=entries)        
        
        cis_data = d.get('cell_import_settings', [])
        if isinstance(cis_data, list):
            for entry in cis_data:
                inst_settings = []
                for inst in entry.get('instance_settings', []):
                    inst_settings.append(InstanceImportSetting(
                        instance_name=inst.get('instance_name', ''),
                        device_name=inst.get('device_name', ''),
                        import_mode=ImportMode(inst.get('import_mode',
                                                        ImportMode.TECH_CELL_MAPPING.value)),
                        static_library=inst.get('static_library', ''),
                        static_cell=inst.get('static_cell', '')
                    ))
                settings.cell_import_settings.append(CellImportSetting(
                    cell_name=entry.get('cell_name', ''),
                    import_mode=ImportMode(entry.get('import_mode',
                                                     ImportMode.NEW_CELL.value)),
                    static_library=entry.get('static_library', ''),
                    static_cell=entry.get('static_cell', ''),
                    instance_settings=inst_settings,
                ))
        
        origin_x_str = d.get('origin_x', None)
        if origin_x_str is not None:
            settings.origin_x = float(origin_x_str)

        origin_y_str = d.get('origin_y', None)
        if origin_y_str is not None:
            settings.origin_y = float(origin_y_str)
        
        limit_columns_str = d.get('limit_columns', None)
        if limit_columns_str is not None:
            settings.limit_columns = bool(int(limit_columns_str))
        
        max_columns_str = d.get('max_columns', None)
        if max_columns_str is not None:
            settings.max_columns = int(max_columns_str)
        
        pitch_str = d.get('pitch', None)
        if pitch_str is not None:
            settings.pitch = float(pitch_str)

        return settings
    
    def dict(self) -> Dict:
        d = {
            'source_path': str(self.source_path),
            'file_format': self.file_format.value,
            'hierarchy_mode': self.hierarchy_mode.value,
            'cell_map': {
                'entries': [
                    {
                        'netlist_device': e.netlist_device,
                        'layout_cell_library': e.layout_cell_library,
                        'layout_cell': e.layout_cell,
                        'layout_cell_type': e.layout_cell_type.value,
                        'parameter_mapping': {'entries': e.parameter_mapping.entries}
                    }
                    for e in self.cell_map.entries
                ]
            },
            'cell_import_settings': [
                {
                    'cell_name': cis.cell_name,
                    'import_mode': cis.import_mode.value,
                    'static_library': cis.static_library,
                    'static_cell': cis.static_cell,
                    'instance_settings': [
                        {
                            'instance_name': inst.instance_name,
                            'device_name': inst.device_name,
                            'import_mode': inst.import_mode.value,
                            'static_library': inst.static_library,
                            'static_cell': inst.static_cell,
                        }
                        for inst in cis.instance_settings
                    ]
                }
                for cis in self.cell_import_settings
            ],
            'origin_x': str(self.origin_x),
            'origin_y': str(self.origin_y),
            'limit_columns': str(int(self.limit_columns)),
            'max_columns': str(self.max_columns),
            'pitch': str(self.pitch)
        }
        return d
