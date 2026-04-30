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

import pya

from pathlib import Path
from typing import *

from netlist_import_config import *
from netlist_parser import NetlistParser, NetlistError, NetlistCell, DeviceInstance


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
                          position: pya.DVector) -> Tuple[pya.Cell, pya.DCellInstArray]:
        print(f"NetlistImporter.add_cell_instance(cell_name={cell_name}, params={params})")
        cell = self.layout.create_cell(cell_name, cell_lib, params)
        inst = pya.DCellInstArray(cell, pya.DTrans(position))
        parent_cell.insert(inst)
        return cell, inst
    
    def _should_import_cell(self, cell: NetlistCell) -> bool:
        """Check whether a cell should be imported based on its ImportSetting."""
        cis = self.config.cell_import_setting_for(cell.name)
        if cis is None:
            return True  # no setting → import by default
        return cis.import_setting != ImportSetting.IGNORE

    def _get_cell_import_setting(self, cell: NetlistCell) -> ImportSetting:
        """Return the ImportSetting for a cell."""
        cis = self.config.cell_import_setting_for(cell.name)
        if cis is None:
            return ImportSetting.NEW_CELL
        return cis.import_setting

    def _should_import_instance(self, cell: NetlistCell, inst: DeviceInstance) -> bool:
        """Check whether an instance should be imported based on its ImportSetting."""
        cis = self.config.cell_import_setting_for(cell.name)
        if cis is None:
            return True
        sis = cis.instance_setting_for(inst.name)
        if sis is None:
            return True
        return sis.import_setting != ImportSetting.IGNORE

    def _get_instance_import_setting(self, cell: NetlistCell, inst: DeviceInstance) -> ImportSetting:
        """Return the ImportSetting for an instance."""
        cis = self.config.cell_import_setting_for(cell.name)
        if cis is None:
            return ImportSetting.TECH_CELL_MAPPING
        sis = cis.instance_setting_for(inst.name)
        if sis is None:
            return ImportSetting.TECH_CELL_MAPPING
        return sis.import_setting

    def _place_instance_via_tech_mapping(self,
                                         inst: DeviceInstance,
                                         lay_cell: pya.Cell,
                                         position: pya.DVector) -> Optional[Tuple[pya.Cell, pya.DCellInstArray]]:
        """Place an instance using the tech cell mapping (cell_map)."""
        cell_map = self.config.cell_map.map_entry_for_device(inst.device_name)
        if cell_map is None:
            print(f"  Warning: no tech cell mapping for device '{inst.device_name}' "
                  f"(instance '{inst.name}'), skipping.")
            return None
        return self.add_cell_instance(
            self.layout,
            cell_map.layout_cell,
            cell_map.layout_cell_library,
            inst.parameters,
            lay_cell,
            position,
        )

    def _place_instance_via_external_static(self,
                                            inst: DeviceInstance,
                                            lay_cell: pya.Cell,
                                            position: pya.DVector) -> Optional[Tuple[pya.Cell, pya.DCellInstArray]]:
        """Place an instance as an external static cell reference.
        
        Uses the device_name directly as the cell name (no library, no params).
        """
        device = inst.device_name
        if not device:
            print(f"  Warning: instance '{inst.name}' has no device name, skipping.")
            return None

        # Find or create the cell by name (no library)
        cell = self.layout.cell(device)
        if cell is None:
            cell = self.layout.create_cell(device)
            print(f"  Created empty static cell '{device}' for instance '{inst.name}'")

        cell_inst = pya.DCellInstArray(cell, pya.DTrans(position))
        lay_cell.insert(cell_inst)
        return cell, cell_inst

    def import_netlist_into_layout(self):
        parser = NetlistParser()
        netlist = parser.parse(str(self.config.source_path))   # might raise NetlistError
        
        ## parent_cell = pya.CellView.active().cell
        
        for cell in netlist.cells:
            if not self._should_import_cell(cell):
                print(f"Skipping cell '{cell.name}' (ImportSetting: Ignore)")
                continue

            cell_setting = self._get_cell_import_setting(cell)
            print(f"Importing cell '{cell.name}' (ImportSetting: {cell_setting.ui_label})")
            
            lay_cell = self.layout.create_cell(cell.name)
            
            for port in cell.ports:
                # TODO: perhaps add port pins, but which metal?
                pass
            
            x = self.config.origin_x
            y = self.config.origin_y
            columns = 0
            rows = 0
            row_height = 0.0
            
            for inst in cell.instances:
                if not self._should_import_instance(cell, inst):
                    print(f"  Skipping instance '{inst.name}' in cell '{cell.name}' (Ignore)")
                    continue
            
                inst_setting = self._get_instance_import_setting(cell, inst)
                position = pya.DVector(x, y)
                placed_inst = None
            
                if inst_setting == ImportSetting.TECH_CELL_MAPPING:
                    result = self._place_instance_via_tech_mapping(
                        inst, lay_cell, position)

                elif inst_setting == ImportSetting.EXTERNAL_STATIC_CELL:
                    result = self._place_instance_via_external_static(
                        inst, lay_cell, position)

                else:
                    print(f"  Warning: unhandled instance ImportSetting "
                          f"'{inst_setting.value}' for '{inst.name}', skipping.")
                    continue
                
                if result is None:
                    continue
                placed_cell, placed_inst = result

                columns += 1
                bbox = placed_cell.dbbox()
                row_height = max(row_height, bbox.height())
                if self.config.limit_columns and\
                   self.config.max_columns <= columns:
                    y += row_height + self.config.spacing
                    columns = 0
                    rows += 1
                    row_height = 0.0
                    x = self.config.origin_x
                else:
                    x += bbox.width() + self.config.spacing

            pya.CellView.active().cell = lay_cell
            
        self.layout.refresh()
        lv = pya.LayoutView.current()
        lv.zoom_fit()
        lv.select_all()
        lv.max_hier()


if __name__ == "__main__":
    test_path = '/Users/martin/Source/ihp_ref_layouts/ihp-sg13g2-ams-chip-template/macros/inverter/netlist/schematic/inverter_magic.spice'

    cell_map = CellMap([
        CellMapEntry('sg13_lv_nmos', 'SG13_dev', 'nmos', CellType.PCELL, ParameterMapping('w=@w l=@l ng=@ng m=@m')),
        CellMapEntry('sg13_lv_pmos', 'SG13_dev', 'pmos', CellType.PCELL, ParameterMapping('w=@w l=@l ng=@ng m=@m')),
    ])

    config = NetlistImportConfig(
        source_path=test_path,
        file_format=NetlistFileFormat.SPICE_SIMULATION_NETLIST,
        hierarchy_mode=HierarchyMode.PRESERVE_HIERARCHY,
        cell_map=cell_map,
        max_columns=3,
        spacing=1.0
    )

    layout = pya.CellView.active().layout()
    
    importer = NetlistImporter(config, layout)
    importer.import_netlist_into_layout()
    
    
