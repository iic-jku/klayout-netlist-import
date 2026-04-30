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
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
import os 
from pathlib import Path
import re
import sys
import threading
import traceback
from typing import *

import pya

from klayout_plugin_utils.debugging import debug, Debugging
from klayout_plugin_utils.event_loop import EventLoop
from klayout_plugin_utils.qt_helpers import qmessagebox_critical
from klayout_plugin_utils.str_enum_compat import StrEnum

from netlist_import_config import NetlistImportConfig
from netlist_import_dialog import NetlistImportDialog
from previous_ui_settings import PreviousUISettings

#--------------------------------------------------------------------------------

path_containing_this_script = os.path.realpath(os.path.join(os.path.dirname(__file__)))

#--------------------------------------------------------------------------------

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
        cw = pya.CellView.active()
        if cw is None or cw.cell is None:
            qmessagebox_critical('Error', 'Import failed', 'No layout open to import into')
            return
        
        config = PreviousUISettings.load()
        
        mw = pya.MainWindow.instance()
        try:
            self.dialog = NetlistImportDialog(config=config, parent=mw)
        except Exception as e:
            print(f"ERROR: Failed to open netlist import dialog due to exception: {e}")
            traceback.print_exc()
            return
        
        result = self.dialog.exec_()
        