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
from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path
import sys
import traceback
from typing import *

from klayout_plugin_utils.debugging import debug, Debugging
from klayout_plugin_utils.str_enum_compat import StrEnum
from klayout_plugin_utils.dataclass_dict_helpers import dataclass_from_dict

from netlist_import_cell_map import *


@dataclass
class NetlistPDKInfo:
    tech_name: str
    cell_map: CellMap
    
    @classmethod
    def read_json(cls, path: Path) -> NetlistPDKInfo:
        with open(path) as f:
            data = json.load(f)
            return dataclass_from_dict(cls, data)
        
    def write_json(self, path: Path):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(asdict(self), f, indent=4)


class NetlistPDKInfoFactory:
    def __init__(self, search_path: List[Path]):
        self._pdk_infos_by_tech_name: Dict[str, NetlistPDKInfo] = {}
        
        json_files = sorted({f for p in search_path for f in p.glob('*.json')})
        for f in json_files:
            try:
                pdk_info = NetlistPDKInfo.read_json(f)
                self._pdk_infos_by_tech_name[pdk_info.tech_name] = pdk_info
            except Exception as e:
                traceback.print_exc()
                print(f"Failed to parse PDK info file {f}, skipping this file…", e)
                
    def pdk_info(self, tech_name: str) -> Optional[NetlistPDKInfo]:
        return self._pdk_infos_by_tech_name.get(tech_name, None)
            
    @property
    def pdk_infos_by_tech_name(self) -> Dict[str, NetlistPDKInfo]:
        return self._pdk_infos_by_tech_name
