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


class NetlistReader(pya.NetlistSpiceReaderDelegate):
    def __init__(self, 
                 config: NetlistImportConfig):
        super().__init__()
        self.config = config
        self.layout = layout

    def read_netlist(self) -> pya.Netlist:
        netlist = pya.Netlist()
        netlist_reader = pya.NetlistSpiceReader(self)
        ## netlist_reader = pya.NetlistSpiceReader()
        netlist.read(self.config.source_path, netlist_reader)
        return netlist

    def start(self, netlist: pya.Netlist):
        # print(f"NetlistReader.start()")
        super.start(netlist)

    def wants_subcircuit(self, name: str) -> bool:
        # print(f"NetlistReader.wants_subcircuit({name})")
        ## TODO!!!
        ## return name in ('SG13_LV_NMOS', 'SG13_LV_PMOS')
        super.wants_subcircuit(name)
    
    def parse_element(self,
                      s: str,
                      el: str) -> pya.ParseElementData:
        # print(f"NetlistImporter.parse_element(s={s!r}, el={el!r})")
        ed = super().parse_element(s, el)
        return ed
    
    def element(self, 
                circuit: pya.Circuit, 
                el: str, 
                name: str, 
                model: str, 
                value: float, 
                nets: List[pya.Net], 
                params: Dict[str, Any]) -> bool:
        # print(f"NetlistImporter.element(element={el}, name={name}, model={model}, value={value}, params={params})")
        # return True
        super.element(circuit, el, name, model, value, nets, params)
    
    def finish(self, netlist: pya.Netlist):
        # print(f"NetlistImporter.finish()")
        super.finish(netlist)
    
    def translate_net_name(self, name: str) -> str:
        super.translate_net_name(name)
        #return name
    

if __name__ == "__main__":
    test_path = '/Users/martin/Source/ihp_ref_layouts/ihp-sg13g2-ams-chip-template/macros/inverter/netlist/schematic/inverter_magic.spice'

    cell_map = CellMap([
        CellMapEntry('sg13_lv_nmos', 'nmos', CellType.PCELL, ParameterMapping('w=@w l=@l ng=@ng m=@m')),
        CellMapEntry('sg13_lv_pmos', 'pmos', CellType.PCELL, ParameterMapping('w=@w l=@l ng=@ng m=@m')),
    ])

    config = NetlistImportConfig(
        source_path=test_path,
        file_format=NetlistFileFormat.SPICE_SIMULATION_NETLIST,
        hierarchy_mode=HierarchyMode.PRESERVE_HIERARCHY,
        cell_map=cell_map
    )

    layout = pya.CellView.active().layout()
    
    r = NetlistReader(config)
    n = r.read_netlist()
    
    
    
