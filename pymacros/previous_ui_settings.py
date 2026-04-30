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

import traceback

import pya

from klayout_plugin_utils.debugging import debug, Debugging

from netlist_import_config import NetlistImportConfig


CONFIG_KEY__netlist_import_config = 'netlist_import_config'


class PreviousUISettings:
    @staticmethod
    def load() -> NetlistImportConfig:
        mw = pya.MainWindow.instance()

        settings: NetlistImportConfig
        try:
            settings_str = mw.get_config(CONFIG_KEY__netlist_import_config)
            settings = NetlistImportConfig()        
            if settings_str is not None:
                d = pya.AbstractMenu.unpack_key_binding(settings_str)
                settings = NetlistImportConfig.from_dict(d)
        except Exception as e:
            print(f"ERROR: Failed to restore import settings, proceeding with defaults due to exception: {e}")
            traceback.print_exc()
            settings = NetlistImportConfig()
        return settings
    
    @staticmethod
    def save(settings: NetlistImportConfig):
        mw = pya.MainWindow.instance()
        
        settings_str = pya.AbstractMenu.pack_key_binding(settings.dict())
        mw.set_config(CONFIG_KEY__netlist_import_config, settings_str)
    
    
