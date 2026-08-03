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
import os 
import traceback
from typing import *

import pya

from klayout_plugin_utils.debugging import debug, Debugging
from klayout_plugin_utils.event_loop import EventLoop
from klayout_plugin_utils.qt_helpers import qmessagebox_critical
from klayout_plugin_utils.str_enum_compat import StrEnum

from klayout_netlist_importer.netlist_import_config import NetlistImportConfig
from klayout_netlist_importer.netlist_import_dialog import NetlistImportDialog
from klayout_netlist_importer.previous_import_ui_settings import PreviousUISettings


class NetlistImportPluginFactory(pya.PluginFactory):
    def __init__(self):
        super().__init__()
        
        if Debugging.DEBUG:
            debug("NetlistImportPluginFactory.ctor")
        
        self.has_tool_entry = False
        self.register(-1000, "klayout_netlist_import", "Netlist Import")
        
        try:
            mw = pya.MainWindow.instance()
            
            self.setup()
        except Exception as e:
            print("NetlistImportPluginFactory.ctor caught an exception", e)
            traceback.print_exc()
  
    def configure(self, name: str, value: str) -> bool:
        return False

    def reset_menu(self):
        if Debugging.DEBUG:
            debug("NetlistImportPluginFactory.reset_menu")
        
        mw = pya.MainWindow.instance()
        menu = mw.menu()
        
        menu.insert_separator("file_menu.import_menu.end", "import_netlist_separator")

        action = pya.Action()
        action.title = "Netlist"
        action.on_triggered += lambda: self.import_netlist()
        menu.insert_item(f"file_menu.import_menu.end", f"import_netlist", action)

    def setup(self):
        if Debugging.DEBUG:
            debug(f"NetlistImportPluginFactory.setup")
    
        self.reset_menu()

    def stop(self):
        pass
        
    def import_netlist(self):
        cv = pya.CellView.active()
        if cv is None or cv.cell is None:
            qmessagebox_critical('Error', 'Import failed', 'No layout open to import into')
            return
        
        tech = cv.layout().technology()
        
        config = PreviousUISettings.load(tech)
        
        if self._pdk_cell_map_outdated(config, tech):
            if self._prompt_restore_default_cell_map(tech):
                config.tech_cell_map = NetlistImportConfig.default_for_tech(tech).tech_cell_map
                config.pdk_cell_map_checksum = NetlistImportConfig.current_pdk_checksum_for_tech(tech)
        
        mw = pya.MainWindow.instance()
        try:
            self.dialog = NetlistImportDialog(config=config, parent=mw)
            result = self.dialog.exec_()
        except Exception as e:
            print(f"ERROR: Failed to open netlist import dialog due to exception: {e}")
            traceback.print_exc()
            return
    
    @staticmethod
    def _pdk_cell_map_outdated(config: NetlistImportConfig, tech: pya.Technology) -> bool:   # NEW
        """True if the installed PDK cell-mapping JSON differs from the checksum
        stored at last save. Configs with no stored checksum (pre-dating this
        feature) are treated as outdated."""
        if not config.pdk_cell_map_checksum:
            return True
        current = NetlistImportConfig.current_pdk_checksum_for_tech(tech)
        return current is not None and current != config.pdk_cell_map_checksum
    
    @staticmethod
    def _prompt_restore_default_cell_map(tech: pya.Technology) -> bool:                      # NEW
        answer = pya.QMessageBox.question(
            pya.MainWindow.instance(),
            'PDK Cell Mapping Changed',
            f"The cell mapping shipped with technology '{tech.name}' has changed "
            f"since your last saved import settings.\n\n"
            f"Restore the technology's default cell mapping before importing?",
            pya.QMessageBox.Yes | pya.QMessageBox.No,
            pya.QMessageBox.No
        )
        return answer == pya.QMessageBox.Yes