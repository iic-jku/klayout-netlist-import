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
from typing import *

from klayout_plugin_utils.library_helper import LibraryHelper

import pya


class PageBase(pya.QWidget):
    def widget(self) -> pya.QWidget:
        raise NotImplementedError(f"{type(self).__name__} must implement widget()")

    def validate_lib_cell_combo(self,
                                lib_cb: pya.QComboBox,
                                cell_cb: pya.QComboBox,
                                *,
                                library_helper: LibraryHelper,
                                prefer_item_data: bool = False) -> None:
        """Set a red background on *lib_cb*/*cell_cb* if their current value
        does not resolve to a real library/cell. 
        Shared by NetlistSourcePage and TechCellMappingPage.

        prefer_item_data:
            When True, read the cell name from the combo's current item data
            (used where entries are stored as data, e.g. "Ⓟ name" / "Ⓢ name"
            display text with the bare name as data). 
            When False, fall back to the combo's plain text.
        """
        lib_name = lib_cb.currentText.strip()
        lib_valid = bool(lib_name) and lib_name in library_helper.get_library_names()

        if prefer_item_data:
            idx = cell_cb.currentIndex
            cell_name = (cell_cb.itemData(idx)
                         if idx >= 0 and cell_cb.itemData(idx)
                         else cell_cb.currentText.strip())
        else:
            cell_name = cell_cb.currentText.strip()

        cell_valid = False
        if lib_valid and cell_name:
            cell_valid = cell_name in library_helper.get_library_cell_names(lib_name)

        red = "QComboBox { background-color: #ffcccc; }"
        ok = ""
        lib_cb.setStyleSheet(red if not lib_valid else ok)
        cell_cb.setStyleSheet(red if not cell_valid else ok)