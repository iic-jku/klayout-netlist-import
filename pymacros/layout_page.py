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

from abc import ABC
import traceback
from typing import *

import pya

from klayout_plugin_utils.debugging import debug, Debugging

from netlist_import_config import LayoutConfig
from page_base import PageBase
from ui_loader import load_ui


class LayoutPage(PageBase):
    def __init__(self):
        super().__init__()
        
        self._setup()

    def _setup(self):
        self._widget = load_ui('NetlistImportConfig_LayoutPage.ui', self)
        
    def widget(self) -> pya.QWidget:
        return self._widget
    
    def config_from_ui(self) -> LayoutConfig:
        return LayoutConfig(
            origin_x=self._widget.origin_x_sb.value,
            origin_y=self._widget.origin_y_sb.value,
            limit_columns=self._widget.limit_columns_cb.checked,
            max_columns=self._widget.max_columns_sb.value,
            pitch=self._widget.pitch_sb.value
        )
    
    def update_ui_from_config(self, config: LayoutConfig):
        self._widget.origin_x_sb.setValue(config.origin_x)
        self._widget.origin_y_sb.setValue(config.origin_y)
        
        self._widget.limit_columns_cb.setChecked(config.limit_columns)
        self._widget.max_columns_sb.setValue(config.max_columns)
        self._widget.pitch_sb.setValue(config.pitch)        
    
