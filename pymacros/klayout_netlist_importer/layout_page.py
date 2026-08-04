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
from klayout_plugin_utils.ui_loader import load_ui

from klayout_netlist_importer.netlist_import_config import LayoutConfig
from klayout_netlist_importer.page_base import PageBase


class LayoutPage(PageBase):
    def __init__(self):
        super().__init__()
        
        self._setup()

    def _setup(self):
        self._widget = load_ui('NetlistImportConfig_LayoutPage.ui', self)
        self._widget.fixed_placement_mode_rb.toggled.connect(self._on_placement_mode_toggled)
        self._widget.packed_placement_mode_rb.toggled.connect(self._on_placement_mode_toggled)
        self._on_placement_mode_toggled()
        
    def widget(self) -> pya.QWidget:
        return self._widget
    
    def config_from_ui(self) -> LayoutConfig:
        return LayoutConfig(
            origin_x=self._widget.origin_x_sb.value,
            origin_y=self._widget.origin_y_sb.value,
            limit_columns=self._widget.limit_columns_cb.checked,
            max_columns=self._widget.max_columns_sb.value,
            pitch=self._widget.pitch_sb.value,
            padding=self._widget.padding_sb.value,
            avoid_overlaps=self._widget.packed_placement_mode_rb.checked,
        )
    
    def update_ui_from_config(self, config: LayoutConfig):
        self._widget.origin_x_sb.setValue(config.origin_x)
        self._widget.origin_y_sb.setValue(config.origin_y)
        
        self._widget.limit_columns_cb.setChecked(config.limit_columns)
        self._widget.max_columns_sb.setValue(config.max_columns)
        self._widget.pitch_sb.setValue(config.pitch)
        self._widget.padding_sb.setValue(config.padding)

        self._widget.packed_placement_mode_rb.setChecked(config.avoid_overlaps)
        self._widget.fixed_placement_mode_rb.setChecked(not config.avoid_overlaps)    
    
    def _on_placement_mode_toggled(self, checked: bool = False):
        fixed = self._widget.fixed_placement_mode_rb.checked
        self._widget.pitch_sb.setEnabled(fixed)
        self._widget.padding_sb.setEnabled(not fixed)
