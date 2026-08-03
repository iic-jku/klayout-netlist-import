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
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import *

import pya

from klayout_plugin_utils.debugging import debug, Debugging
from klayout_plugin_utils.netlist_parser import DeviceInstance
from klayout_plugin_utils.str_enum_compat import DualStrEnum

#--------------------------------------------------------------------------------

# Matches optional sign, digits (with optional decimal point), optional exponent.
# e.g. "3", "3.5", "-3.5e-2", ".5"
_NUMBER_RE = re.compile(r'^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$')

_SPICE_SUFFIXES = {
        't': 1e12, 'g': 1e9, 'meg': 1e6, 'k': 1e3,
        'm': 1e-3, 'u': 1e-6, 'n': 1e-9, 'p': 1e-12,
        'f': 1e-15, 'a': 1e-18,
    }


def parse_numeric(value: str) -> float:
    """Convert string to float, handling SPICE suffixes."""
    value = value.strip().lower()
    for suffix, multiplier in sorted(_SPICE_SUFFIXES.items(), key=lambda x: -len(x[0])):
        if value.endswith(suffix):
            numeric_part = value[:-len(suffix)]
            if _NUMBER_RE.match(numeric_part):
                return float(numeric_part) * multiplier
            # suffix matched but remainder isn't numeric — keep trying shorter suffixes

    if _NUMBER_RE.match(value):
        f = float(value)
        i = int(f)
        return i if i == f else f

    return value


def convert_value_for_param(value: str, param_decl: pya.PCellParameterDeclaration) -> Any:
    """Coerce a raw netlist parameter string into the type declared by the PCell."""
    t = param_decl.type
    try:
        if t == pya.PCellParameterDeclaration.TypeDouble:
            return float(parse_numeric(value))
        elif t == pya.PCellParameterDeclaration.TypeInt:
            return int(parse_numeric(value))
        elif t == pya.PCellParameterDeclaration.TypeBoolean:
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ('1', 'true', 't', 'yes')
        elif t == pya.PCellParameterDeclaration.TypeString:
            return str(value)
        elif t == pya.PCellParameterDeclaration.TypeLayer:
            # Expect a layer/purpose-like string; leave to PCell layer resolution
            return str(value)
        else:
            # TypeList, TypeShape, TypeHandle, … — pass through unmodified
            return value
    except (ValueError, TypeError):
        if Debugging.DEBUG:
            debug(f"convert_value_for_param: "
                  f"failed to convert '{value}' to type {t} for param "
                  f"'{param_decl.name}', passing through raw value")
        return value

#--------------------------------------------------------------------------------

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
    multiplier: str = ''
    netlist_node_order: List[str] = field(default_factory=list)
    
    def build_local_net_map(self, inst: DeviceInstance) -> Dict[str, str]:
        """Map this entry's PCell pin/terminal names to the net names connected
        to a specific instance, using `netlist_node_order` (positionally aligned with
        the SPICE node order for this device).

        Returns an empty dict (with a debug message) if `netlist_node_order` hasn't
        been configured for this entry yet.
        """
        if not self.netlist_node_order:
            if Debugging.DEBUG:
                debug(f"CellMapEntry.build_local_net_map: "
                      f"no netlist_node_order configured for '{self.layout_cell}', "
                      f"cannot build LOCAL_NET_MAP for instance '{inst.name}'")
            return {}

        if len(self.netlist_node_order) != len(inst.nodes) and Debugging.DEBUG:
            debug(f"CellMapEntry.build_local_net_map: "
                  f"netlist_node_order length ({len(self.netlist_node_order)}) != node count "
                  f"({len(inst.nodes)}) for instance '{inst.name}' "
                  f"of device '{self.netlist_device}'; mapping truncated to the shorter list")

        return dict(zip(self.netlist_node_order, inst.nodes))

    def map_parameters(self,
                       netlist_params: Dict,
                       pcell_decl: pya.PCellDeclaration) -> Tuple[Dict, int]:
        """Apply parameter_mapping to translate netlist params → PCell params.

        Mapping format: pcell_param=@netlist_param  or  pcell_param=literal
        The PCell declaration is consulted so each value is coerced to the
        type the PCell actually expects (double/int/string/bool/…), instead
        of guessing a single numeric conversion for everything.
        """
        param_decls_by_name = {p.name: p for p in pcell_decl.get_parameters()}

        result = {}
        for pcell_key, expr in self.parameter_mapping.entries.items():
            if expr.startswith('@'):
                netlist_key = expr[1:]
                raw_value = netlist_params.get(netlist_key, None)
            else:
                raw_value = expr

            if raw_value is None or raw_value == '':
                # TODO: log error
                continue

            param_decl = param_decls_by_name.get(pcell_key, None)
            if param_decl is None:
                if Debugging.DEBUG:
                    debug(f"CellMapEntry.map_parameters: "
                          f"unknown PCell parameter '{pcell_key}' on '{self.layout_cell}', "
                          f"falling back to numeric parse")
                result[pcell_key] = parse_numeric(raw_value)
                continue

            result[pcell_key] = convert_value_for_param(raw_value, param_decl)

        multiplier = 1
        if self.multiplier:
            mult_key = self.multiplier
            if mult_key.startswith('@'):
                mult_key = mult_key[1:]
            mult_param = netlist_params.get(mult_key, None)
            if mult_param is not None and mult_param != '':
                multiplier = int(parse_numeric(mult_param))

        return result, multiplier

    def resolve_layout_cell(self,
                            layout: pya.Layout,
                            netlist_params: Dict) -> Tuple[pya.Cell, int]:
        """Create/find the layout cell for this entry, given a live pya.Layout.

        Raises NetlistError if this entry declares a PCell that cannot be
        found in the resolved library.
        """
        lib = pya.Library.library_by_name(self.layout_cell_library, layout.technology().name)
        if lib is None:
            raise NetlistError(
                f"Library '{self.layout_cell_library}' not found "
                f"in technology '{layout.technology().name}'"
            )

        multiplier = 1

        if self.layout_cell_type == CellType.PCELL:
            pcell_decl = lib.layout().pcell_declaration(self.layout_cell)
            if pcell_decl is None:
                raise NetlistError(
                    f"PCell '{self.layout_cell}' not found in library '{self.layout_cell_library}'"
                )

            pcell_params, multiplier = self.map_parameters(netlist_params, pcell_decl)
            cell = layout.create_cell(self.layout_cell, self.layout_cell_library, pcell_params)
        else:
            cell = layout.create_cell(self.layout_cell, self.layout_cell_library)

        return cell, multiplier
                
    
@dataclass
class CellMap:
    entries: List[CellMapEntry] = field(default_factory=list)

    def map_entry_for_device(self, netlist_device: str) -> Optional[CellMapEntry]:
        for e in self.entries:
            if e.netlist_device.lower() == netlist_device.lower():
                return e
        return None

    def save_json(self, path: Path):
        """Save cell map entries to a JSON file."""
        data = self.dict()
        with open(str(path), 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load_json(cls, path: Path) -> CellMap:
        """Load cell map entries from a JSON file."""
        with open(str(path), 'r', encoding='utf-8') as f:
            raw = json.load(f)
        cell_map_data = raw.get('cell_map', raw)
        return cls.from_dict(cell_map_data)

    @classmethod
    def from_dict(cls, d: Dict) -> CellMap:
        entries = []
        for item in d.get('entries', []):
            entries.append(CellMapEntry(
                netlist_device=item.get('netlist_device', ''),
                layout_cell_library=item.get('layout_cell_library', ''),
                layout_cell=item.get('layout_cell', ''),
                layout_cell_type=CellType(item.get('layout_cell_type', CellType.PCELL.value)),
                parameter_mapping=ParameterMapping(
                    entries=item.get('parameter_mapping', {}).get('entries', {})
                ),
                multiplier=item.get('multiplier', ''),
                netlist_node_order=list(item.get('netlist_node_order', [])),
            ))
        return cls(entries=entries)

    def dict(self) -> Dict:    
        data = {
            'entries': [
                {
                    'netlist_device': e.netlist_device,
                    'layout_cell_library': e.layout_cell_library,
                    'layout_cell': e.layout_cell,
                    'layout_cell_type': e.layout_cell_type.value,
                    'parameter_mapping': {'entries': e.parameter_mapping.entries},
                    'multiplier': e.multiplier,
                    'netlist_node_order': list(e.netlist_node_order),
                }
                for e in self.entries
            ]
        }
        return data
