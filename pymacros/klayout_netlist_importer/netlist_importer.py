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
import json
from pathlib import Path
import re
import traceback
from typing import *

import pya

from klayout_plugin_utils.debugging import debug, Debugging
from klayout_plugin_utils.netlist_parser import NetlistParser, Netlist, NetlistError, NetlistCell, DeviceInstance

from klayout_netlist_importer.netlist_import_config import *
from klayout_netlist_importer.netlist_import_report import NetlistImportReport
from klayout_netlist_importer.grid_placer import GridPlacer, GridPosition

from klayout_plugin_utils.layout_connectivity_info import (
    PROPERTY_KEY__INSTANCE_INFO__VERSION,
    PROPERTY_KEY__INSTANCE_INFO__LIB_NAME,
    PROPERTY_KEY__INSTANCE_INFO__CELL_NAME,
    PROPERTY_KEY__INSTANCE_INFO__INSTANCE_NAME,
    PROPERTY_KEY__INSTANCE_INFO__HIERARCHY_PATH,
    PROPERTY_KEY__INSTANCE_INFO__ORIGINAL_INSTANCE_PARAMS,
    PROPERTY_KEY__INSTANCE_INFO__LOCAL_NET_MAP,
    PROPERTY_KEY__INSTANCE_INFO__GLOBAL_NET_MAP,
)

#--------------------------------------------------------------------------------

INSTANCE_INFO_VERSION = '1'

#--------------------------------------------------------------------------------


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

    def import_netlist_into_layout(self) -> NetlistImportReport:
        cv = pya.CellView.active()
        top_cell_name = cv.cell.name
    
        parser = NetlistParser()
        netlist = parser.parse(str(self.config.netlist_source_config.source_path), 
                               implicit_top_cell_name=top_cell_name)   # might raise NetlistError
        
        report = NetlistImportReport()
        
        m = self.config.netlist_source_config.hierarchy_mode
        if m == HierarchyMode.PRESERVE_HIERARCHY:
            self._import_hierarchical(netlist, cv.cell, report)
        # elif m == HierarchyMode.FLATTEN:
        #    self._import_flattened(netlist)
    
        return report
    
    def _import_hierarchical(self, netlist: Netlist, current_top_cell: pya.Cell, report: NetlistImportReport):
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
                report.add_cell_success(nc.name)
            elif mode == ImportMode.EXTERNAL_STATIC_CELL:
                lib_name = cis.static_library
                cell_name = cis.static_cell
                cell = self._resolve_library_cell(lib_name, cell_name)
                if cell is None:
                    report.add_cell_failure(
                        nc.name,
                        f"Unable to resolve external static cell '{cell_name}' "
                        f"in library '{lib_name}'"
                    )
                    continue
                cell_map[nc.name] = cell
                report.add_cell_success(nc.name)
            elif mode == ImportMode.NETLIST_CELL:
                # This cell is itself defined in the netlist but the user chose
                # "Netlist Cell" — skip creating a new layout cell; it will be
                # referenced from cell_map if another cell already created it.
                pass
            else:
                raise NotImplementedError(f"Unexpected ImportMode enum case {mode}")
        
        netlist_cells_by_name = {nc.name: nc for nc in netlist.all_cells}   # for pin-order lookup on internal cells
        
        # Pass 2: Populate each cell with its instances
        placer = GridPlacer(self.config)

        for nc in netlist.all_cells:
            if nc.name not in cell_map:
                continue
            parent_cell = cell_map[nc.name]
            placer.reset()
            
            for inst in nc.instances:
                child_cell = None
                tech_map_entry: Optional[CellMapEntry] = None
                
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
                            report.add_instance_failure(
                                nc.name, inst.name,
                                f"Unable to resolve tech cell mapping "
                                f"for device {inst.device_name}, parameters {inst.parameters}"
                            )
                            continue
                    except Exception as e:
                        traceback.print_exc()
                        report.add_instance_failure(
                            nc.name, inst.name,
                            f"Unable to resolve tech cell mapping "
                            f"for device {inst.device_name}, parameters {inst.parameters}, due to exception: {e}"
                        )
                        continue
                    child_cell, multiplier, tech_map_entry = result
                elif inst_mode == ImportMode.EXTERNAL_STATIC_CELL:
                    child_cell = self._resolve_library_cell(iis.static_library, iis.static_cell)
                    if child_cell is None:
                        report.add_instance_failure(
                            nc.name, inst.name,
                            f"Unable to resolve external static cell "
                            f"'{iis.static_cell}' in library '{iis.static_library}'"
                        )
                        continue
                elif inst_mode == ImportMode.NETLIST_CELL: # Subcircuit instance → reference the child cell
                    child_cell = cell_map.get(inst.device_name)
                    if child_cell is None:
                        if Debugging.DEBUG:
                            debug(f"NetlistImporter._import_hierarchical:   → NETLIST_CELL: '{inst.device_name}' not in cell_map, skipping")
                        report.add_instance_failure(
                            nc.name, inst.name,
                            f"Subcircuit cell '{inst.device_name}' was not created "
                            f"(likely IGNOREd or failed itself), instance skipped"
                        )
                        continue
                elif inst_mode == ImportMode.NEW_CELL:
                    # Shouldn't normally appear at instance level, treat like netlist cell
                    child_cell = cell_map.get(inst.device_name)
                    if child_cell is None:
                        report.add_instance_failure(
                            nc.name, inst.name,
                            f"Cell '{inst.device_name}' was not created, instance skipped"
                        )
                        continue
                else:
                    raise NotImplementedError(f"Unexpected ImportMode enum case {inst_mode}")

                if child_cell is None:
                    if Debugging.DEBUG:
                        debug(f"[NetlistImporter._import_hierarchical:   → SKIPPED (no cell resolved)")
                    report.add_instance_failure(
                        nc.name, inst.name, "No cell could be resolved for this instance"
                    )
                    continue

                for i in range(multiplier):
                    instance_name = inst.name
                    if multiplier > 1:
                         instance_name += f"${i+1}"

                    try:
                        # Place at next grid position
                        pos = placer.next_position(child_cell)
                        inst_array = pya.DCellInstArray(
                            child_cell.cell_index(),
                            pya.DTrans(pya.DVector(pos.x, pos.y))
                        )

                        layout_inst = parent_cell.insert(inst_array)
                    except Exception as e:
                        traceback.print_exc()
                        report.add_instance_failure(
                            nc.name, instance_name,
                            f"Failed to instantiate {inst.name} {instance_name} for device {inst.device_name}, due to exception: {e}"
                        )
                        break  # NOTE: this will only break out of the multiplier loop,
                               # which is the last code in the block,
                               # so ensure no code is added below!

                    if Debugging.DEBUG:
                        debug(f"NetlistImporter._import_hierarchical:   → PLACED {instance_name} at ({pos.x}, {pos.y})")

                    if tech_map_entry is not None and tech_map_entry.layout_cell_type == CellType.PCELL:
                        self._set_instance_info(
                            layout_inst=layout_inst,
                            lib_name=tech_map_entry.layout_cell_library,
                            cell_name=tech_map_entry.layout_cell,
                            local_net_map=tech_map_entry.build_local_net_map(inst),
                            inst=inst,
                            instance_name=instance_name,
                            hierarchy_path=f"{nc.name}.{instance_name}",
                        )
                    elif inst_mode == ImportMode.NETLIST_CELL:
                        child_nc = netlist_cells_by_name.get(inst.device_name)
                        self._set_instance_info(
                            layout_inst=layout_inst,
                            lib_name='',   # internal cell — not library-backed
                            cell_name=inst.device_name,
                            local_net_map=self._build_subcircuit_local_net_map(child_nc, inst),
                            inst=inst,
                            instance_name=instance_name,
                            hierarchy_path=f"{nc.name}.{instance_name}",
                        )
                    elif inst_mode == ImportMode.EXTERNAL_STATIC_CELL:
                        self._set_instance_info(
                            layout_inst=layout_inst,
                            lib_name=iis.static_library,
                            cell_name=iis.static_cell,
                            local_net_map={},   # no configured pin/port order for ad-hoc static overrides
                            inst=inst,
                            instance_name=instance_name,
                            hierarchy_path=f"{nc.name}.{instance_name}",
                        )
                    
                    report.add_instance_success(nc.name, instance_name)
                
    def _resolve_tech_mapped_cell(self, device_name: str, parameters: Dict) -> Optional[Tuple[pya.Cell, int, CellMapEntry]]:
        """Look up device in cell_map and create/find the layout cell."""
        entry = self.config.tech_cell_map.map_entry_for_device(device_name)
        if entry is None:
            return None
        
        cell, multiplier = entry.resolve_layout_cell(self.layout, parameters)
        return cell, multiplier, entry
    
    def _resolve_library_cell(self, lib_name: str, cell_name: str) -> Optional[pya.Cell]:
        """Resolve a static cell from a library."""
        if not lib_name or not cell_name:
            return None
        return self.layout.create_cell(cell_name, lib_name)
    
    def _set_instance_info(self,
                           layout_inst: pya.Instance,
                           lib_name: str,
                           cell_name: str,
                           local_net_map: Dict[str, str],
                           inst: DeviceInstance,
                           instance_name: str,
                           hierarchy_path: str):
        """Stamp INSTANCE_INFO__* properties onto a freshly-placed instance —
        used for both tech-mapped PCells and internal (NETLIST_CELL) subcircuit
        instances."""

        layout_inst.set_property(PROPERTY_KEY__INSTANCE_INFO__VERSION, INSTANCE_INFO_VERSION)
        layout_inst.set_property(PROPERTY_KEY__INSTANCE_INFO__LIB_NAME, lib_name)
        layout_inst.set_property(PROPERTY_KEY__INSTANCE_INFO__CELL_NAME, cell_name)
        layout_inst.set_property(PROPERTY_KEY__INSTANCE_INFO__INSTANCE_NAME, instance_name)
        layout_inst.set_property(PROPERTY_KEY__INSTANCE_INFO__HIERARCHY_PATH, hierarchy_path)
        layout_inst.set_property(
            PROPERTY_KEY__INSTANCE_INFO__ORIGINAL_INSTANCE_PARAMS,
            json.dumps(inst.parameters)
        )
        layout_inst.set_property(
            PROPERTY_KEY__INSTANCE_INFO__LOCAL_NET_MAP,
            json.dumps(local_net_map)
        )
        # GLOBAL_NET_MAP requires resolving nested subckt call sites; using
        # the local map as a placeholder until top-down hierarchy resolution
        # is implemented in a follow-up.
        layout_inst.set_property(
            PROPERTY_KEY__INSTANCE_INFO__GLOBAL_NET_MAP,
            json.dumps(local_net_map)
        )
        
    def _build_subcircuit_local_net_map(self, 
                                        child_nc: Optional[NetlistCell], 
                                        inst: DeviceInstance) -> Dict[str, str]:
        """Pin→net mapping for a NETLIST_CELL (subcircuit) instance, built from
        the subckt's own port order — the internal-cell counterpart to
        CellMapEntry.build_local_net_map() for tech-mapped PCells."""
        ports = child_nc.ports if child_nc is not None else None
        if not ports:
            if Debugging.DEBUG:
                debug(f"NetlistImporter._build_subcircuit_local_net_map: "
                      f"no port order available for subcircuit '{inst.device_name}', "
                      f"cannot build LOCAL_NET_MAP for instance '{inst.name}'")
            return {}
    
        if len(ports) != len(inst.nodes) and Debugging.DEBUG:
            debug(f"NetlistImporter._build_subcircuit_local_net_map: "
                  f"port count ({len(ports)}) != node count ({len(inst.nodes)}) "
                  f"for instance '{inst.name}' of subcircuit '{inst.device_name}'; "
                  f"mapping truncated to the shorter list")
    
        return dict(zip(ports, inst.nodes))    