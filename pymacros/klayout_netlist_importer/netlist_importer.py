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
from pathlib import Path
from typing import *

import pya

from klayout_plugin_utils.debugging import debug, Debugging
from klayout_plugin_utils.netlist_parser import NetlistParser, Netlist, NetlistError, NetlistCell, DeviceInstance

from klayout_netlist_importer.netlist_import_config import *
from klayout_netlist_importer.grid_placer import GridPlacer, GridPosition


class NetlistImporter(pya.NetlistSpiceReaderDelegate):
    def __init__(self, 
                 config: NetlistImportConfig, 
                 layout: pya.Layout):
        super().__init__()
        self.config = config
        self.layout = layout

    def add_cell_instance(self,
                          layout: pya.Layout,
                          cell_name: str,
                          cell_lib: str,
                          params: Dict[str, Any],
                          parent_cell: pya.Cell,
                          position: pya.DVector) -> Tuple[pya.Cell, pya.Instance, pya.DCellInstArray]:
        if Debugging.DEBUG:
            debug(f"NetlistImporter.add_cell_instance(cell_name={cell_name}, params={params})")
        cell = self.layout.create_cell(cell_name, cell_lib, params)
        inst_arr = pya.DCellInstArray(cell, pya.DTrans(position))
        inst = parent_cell.insert(inst_arr)
        return cell, inst, inst_arr
    
    def _should_import_cell(self, cell: NetlistCell) -> bool:
        """Check whether a cell should be imported based on its ImportMode."""
        cis = self.config.cell_import_setting_for(cell.name)
        if cis is None:
            return True  # no setting → import by default
        return cis.import_mode != ImportMode.IGNORE

    def _get_cell_import_mode(self, cell: NetlistCell) -> ImportMode:
        """Return the ImportMode for a cell."""
        cis = self.config.cell_import_setting_for(cell.name)
        if cis is None:
            return ImportMode.NEW_CELL
        return cis.import_mode

    def _should_import_instance(self, cell: NetlistCell, inst: DeviceInstance) -> bool:
        """Check whether an instance should be imported based on its ImportMode."""
        cis = self.config.cell_import_setting_for(cell.name)
        if cis is None:
            return True
        sis = cis.instance_setting_for(inst.name)
        if sis is None:
            return True
        return sis.import_mode != ImportMode.IGNORE

    def import_netlist_into_layout(self):
        cv = pya.CellView.active()
        top_cell_name = cv.cell.name
    
        parser = NetlistParser()
        netlist = parser.parse(str(self.config.netlist_source_config.source_path), 
                               implicit_top_cell_name=top_cell_name)   # might raise NetlistError
        
        m = self.config.netlist_source_config.hierarchy_mode
        if m == HierarchyMode.PRESERVE_HIERARCHY:
            self._import_hierarchical(netlist, cv.cell)
        # elif m == HierarchyMode.FLATTEN:
        #    self._import_flattened(netlist)
    
    def _import_hierarchical(self, netlist, current_top_cell):
        """Each subckt → its own Cell; subckt instances → CellInst."""
        
        netlist_cell_names = {nc.name for nc in netlist.all_cells}
        cell_map = {}  # netlist cell name → pya.Cell
        
        # Pass 1: Create all cells (bottom-up so children exist before parents)
        for nc in netlist.all_cells:
            cis = self.config.cell_import_setting_for(nc.name)
            mode = cis.import_mode if cis else ImportMode.NEW_CELL
            
            if mode == ImportMode.IGNORE:
                continue
            elif mode == ImportMode.NEW_CELL:
                # Reuse existing cell if it matches the current top cell
                existing = self.layout.cell(nc.name)
                if existing is not None and existing.cell_index() == current_top_cell.cell_index():
                    cell = existing
                    if Debugging.DEBUG:
                        debug(f"NetlistImporter._import_hierarchical: Reusing existing top cell '{nc.name}'")
                else:
                    cell = self.layout.create_cell(nc.name)
                cell_map[nc.name] = cell
            elif mode == ImportMode.EXTERNAL_STATIC_CELL:
                lib_name = cis.static_library
                cell_name = cis.static_cell
                cell = self._resolve_library_cell(lib_name, cell_name)
                cell_map[nc.name] = cell
            elif mode == ImportMode.NETLIST_CELL:
                # This cell is itself defined in the netlist but the user chose
                # "Netlist Cell" — skip creating a new layout cell; it will be
                # referenced from cell_map if another cell already created it.
                pass
            else:
                raise NotImplementedError(f"Unexpected ImportMode enum case {mode}")
        
        # Pass 2: Populate each cell with its instances
        placer = GridPlacer(self.config)
        
        errors: List[str] = []
        
        for nc in netlist.all_cells:
            if nc.name not in cell_map:
                continue
            parent_cell = cell_map[nc.name]
            placer.reset()
            
            for inst in nc.instances:
                child_cell = None
                
                iis = self.config.instance_setting(nc.name, inst.name)
                inst_mode = iis.import_mode if iis else (
                    ImportMode.NETLIST_CELL if inst.device_name in netlist_cell_names
                    else ImportMode.TECH_CELL_MAPPING
                )
                
                multiplier = 1
                
                if inst_mode == ImportMode.IGNORE:
                    continue
                elif inst_mode == ImportMode.TECH_CELL_MAPPING:
                    try:
                        result = self._resolve_tech_mapped_cell(inst.device_name, inst.parameters)
                        if result is None:
                            errors += [
                                f"Unable to resolve tech cell mapping "
                                f"for device {inst.device_name}, parameters {inst.parameters}"
                            ]
                            continue
                    except Exception as e:
                        traceback.print_exc()
                        errors += [
                            f"Unable to resolve tech cell mapping "
                            f"for device {inst.device_name}, parameters {inst.parameters}, due to exception: {e}"
                        ]
                        continue
                    child_cell, multiplier = result
                elif inst_mode == ImportMode.EXTERNAL_STATIC_CELL:
                    child_cell = self._resolve_library_cell(iis.static_library, iis.static_cell)
                elif inst_mode == ImportMode.NETLIST_CELL: # Subcircuit instance → reference the child cell
                    child_cell = cell_map.get(inst.device_name)
                    if child_cell is None:
                        if Debugging.DEBUG:
                            debug(f"NetlistImporter._import_hierarchical:   → NETLIST_CELL: '{inst.device_name}' not in cell_map, skipping")
                        continue
                elif inst_mode == ImportMode.NEW_CELL:
                    # Shouldn't normally appear at instance level, treat like netlist cell
                    child_cell = cell_map.get(inst.device_name)
                else:
                    raise NotImplementedError(f"Unexpected ImportMode enum case {inst_mode}")
                
                if child_cell is None:
                    if Debugging.DEBUG:
                        debug(f"[NetlistImporter._import_hierarchical:   → SKIPPED (no cell resolved)")
                    continue
                
                for i in range(multiplier):
                    instance_name = inst.name
                    if multiplier > 1:
                         instance_name += f"${i+1}"
                
                    # Place at next grid position
                    pos = placer.next_position()
                    inst_array = pya.DCellInstArray(
                        child_cell.cell_index(),
                        pya.DTrans(pya.DVector(pos.x, pos.y))
                    )
                    
                    layout_inst = parent_cell.insert(inst_array)
                    
                    if Debugging.DEBUG:
                        debug(f"NetlistImporter._import_hierarchical:   → PLACED {instance_name} at ({pos.x}, {pos.y})")
                            
    def _resolve_tech_mapped_cell(self, device_name: str, parameters) -> Tuple[pya.Cell, int]:
        """Look up device in cell_map and create/find the layout cell."""
        entry = self.config.tech_cell_map.map_entry_for_device(device_name)
        if entry is None:
            return None
        
        lib = pya.Library.library_by_name(entry.layout_cell_library, self.layout.technology().name)
        if lib is None:
            return None
        
        multiplier = 1
        
        if entry.layout_cell_type == CellType.PCELL:
            # Resolve parameter mapping: netlist params → PCell params
            pcell_params, multiplier = self._map_parameters(entry, parameters)
            cell = self.layout.create_cell(entry.layout_cell, entry.layout_cell_library, pcell_params)
        else:
            cell = self.layout.create_cell(entry.layout_cell, entry.layout_cell_library)
            
        return cell, multiplier
    
    def _resolve_library_cell(self, lib_name: str, cell_name: str) -> str:
        """Resolve a static cell from a library."""
        if not lib_name or not cell_name:
            return None
        return self.layout.create_cell(cell_name, lib_name)
    
    def _map_parameters(self, entry: CellMapEntry, netlist_params: Dict) -> Tuple[Dict, int]:
        """Apply parameter_mapping to translate netlist params → PCell params.
        
        Mapping format: pcell_param=@netlist_param  or  pcell_param=literal
        """
        result = {}
        for pcell_key, expr in entry.parameter_mapping.entries.items():
            if expr.startswith('@'):
                netlist_key = expr[1:]
                param = netlist_params.get(netlist_key, None)
                if param:
                    result[pcell_key] = self._parse_numeric(param)
            else:
                result[pcell_key] = self._parse_numeric(expr)
               
        multiplier = 1 
        if entry.multiplier:
            mult_key = entry.multiplier
            if mult_key.startswith('@'):
                mult_key = mult_key[1:]
            mult_param = netlist_params.get(mult_key, None)
            if mult_param is not None and mult_param != '':
                multiplier = int(mult_param)
        
        return result, multiplier
    
    def _parse_numeric(self, value: str) -> float:
        """Convert string to float, handling SPICE suffixes."""
        suffixes = {
            't': 1e12, 'g': 1e9, 'meg': 1e6, 'k': 1e3,
            'm': 1e-3, 'u': 1e-6, 'n': 1e-9, 'p': 1e-12,
            'f': 1e-15, 'a': 1e-18,
        }
        value = value.strip().lower()
        for suffix, multiplier in sorted(suffixes.items(), key=lambda x: -len(x[0])):
            if value.endswith(suffix):
                try:
                    return float(value[:-len(suffix)]) * multiplier
                except ValueError:
                    pass
        try:
            f = float(value)
            i = int(value)
            return i if i == f else f
        except ValueError:
            return value
    
