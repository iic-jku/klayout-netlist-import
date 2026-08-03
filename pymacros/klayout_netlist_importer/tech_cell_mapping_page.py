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

import os
from pathlib import Path
import traceback
from typing import *

import pya

from klayout_plugin_utils.debugging import debug, Debugging
from klayout_plugin_utils.event_loop import EventLoop
from klayout_plugin_utils.file_system_helpers import FileSystemHelpers
from klayout_plugin_utils.library_helper import LibraryHelper
from klayout_plugin_utils.qt_helpers import (
    compat_QShortCut,
    compat_QTreeWidgetItem_setBackground,
    qmessagebox_critical
)
from klayout_plugin_utils.ui_loader import load_ui

from klayout_netlist_importer.netlist_import_config import *
from klayout_netlist_importer.page_base import PageBase

#--------------------------------------------------------------------------------

# Item-data roles
_PLACEHOLDER_ROLE    = int(pya.Qt.UserRole)
_CELL_NAME_ROLE      = int(pya.Qt.UserRole) + 1
_STATIC_LIBRARY_ROLE = int(pya.Qt.UserRole) + 2
_STATIC_CELL_ROLE    = int(pya.Qt.UserRole) + 3

#--------------------------------------------------------------------------------

class TechCellMappingPage(PageBase):
    def __init__(self, 
                 tech: pya.Technology,
                 library_helper: LibraryHelper):
        super().__init__()
        
        self.tech = tech
        self.library_helper = library_helper
        
        self._cell_type_combos = {}
        self._cell_map_lib_combos = {}
        self._cell_map_cell_combos = {}
        self._auto_switching_cell_type = False
    
        # Observers notified (with no arguments) when the cell map changes.
        # Register via:  page.on_cell_map_changed += [my_callable]
        self.on_cell_map_changed: List[Callable[[], None]] = []
        
        self._setup()
    
    def widget(self) -> pya.QWidget:
        return self._widget
    
    def config_from_ui(self) -> CellMap:
        try:
            return self.cell_map_from_ui(self._widget.cell_map_tw)
        except Exception as e:
            traceback.print_exc()
            return None
    
    def update_ui_from_config(self, cell_map: CellMap):
        self._cell_type_combos.clear()
        
        self._widget.cell_map_tw.blockSignals(True)
        self._widget.cell_map_tw.setRowCount(0)
        
        for row, e in enumerate(cell_map.entries):
            self._widget.cell_map_tw.insertRow(row)
            cells = [
                e.netlist_device,
                None,   # CellType handled separately
                None,   # Library handled separately
                None,   # Cell handled separately
                self._format_parameter_mapping(e.parameter_mapping),
                e.multiplier,
                self._format_netlist_node_order(e.netlist_node_order),
            ]
            for col, value in enumerate(cells):
                if col == 1:  # cell type combo box
                    self._set_cell_type_widget(row, e.layout_cell_type.value)
                elif col == 2:
                    self._set_cell_map_library_widget(row, e.layout_cell_library)
                elif col == 3:
                    self._set_cell_map_cell_widget(row, e.layout_cell, e.layout_cell_library)
                else:
                    self._widget.cell_map_tw.setItem(row, col, self._make_data_item(value))
                
        self._widget.cell_map_tw.blockSignals(False)
    
        self._notify_cell_map_changed()
    
    def select_row_for_device(self, device_name: str):
        # Select the matching row in the cell map table
        table = self._widget.cell_map_tw
        for row in range(table.rowCount):
            item = table.item(row, 0)
            if item and item.text.lower() == device_name.lower():
                table.selectRow(row)
                table.scrollToItem(item)
                break
    
    def _setup(self):
        self._widget = load_ui('NetlistImportConfig_CellMapPage.ui', self)
        p = self._widget
        
        p.tech_cell_mapping_gb.setTitle(f"Cell Mapping (Technology {self.tech.name})")
        
        p.cell_map_add_pb.icon = pya.QIcon(':add_16px')
        p.cell_map_remove_pb.icon = pya.QIcon(':del_16px')

        for pb in (p.cell_map_add_pb, p.cell_map_remove_pb):
            pb.text = ''
            pb.setFixedSize(40, 32)
        
        p.cell_map_add_pb.clicked.connect(self.on_add_cell_mapping)
        p.cell_map_remove_pb.clicked.connect(self.on_remove_cell_mapping)
        
        p.load_map_pb.clicked.connect(self.on_load_cell_map)
        p.save_map_pb.clicked.connect(self.on_save_cell_map)
        
        tree = p.cell_map_tw
        header = tree.horizontalHeader
        header.setSectionResizeMode(0, pya.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, pya.QHeaderView.Fixed)
        tree.setColumnWidth(1, 120)
        header.setSectionResizeMode(2, pya.QHeaderView.Fixed)
        tree.setColumnWidth(2, 200)
        header.setSectionResizeMode(3, pya.QHeaderView.Fixed)
        tree.setColumnWidth(3, 200)
        header.setSectionResizeMode(5, pya.QHeaderView.Fixed)
        tree.setColumnWidth(5, 70)
        header.setStretchLastSection(True)
        
        tree.horizontalHeaderItem(6).setToolTip(
            "Space-separated pin/terminal names, positionally aligned with the "
            "SPICE node order for this device, e.g. 'D G S B'"
        )
        
        tree.setSelectionBehavior(pya.QAbstractItemView.SelectRows)
        tree.setSelectionMode(pya.QAbstractItemView.ExtendedSelection)
        
        tree.itemSelectionChanged.connect(self.on_cell_map_selection_changed)
        tree.currentItemChanged.connect(self.on_cell_map_selection_changed)
        tree.itemChanged.connect(self._on_item_changed)
        tree.cellDoubleClicked.connect(self._on_cell_map_double_clicked)
        
        # NOTE: qt5 vs qt6 has different QShortCut ctor arguments,
        #       thus use our safety wrapper        
        self.shortcuts = [
            compat_QShortCut(pya.QKeySequence("Delete"), 
                             tree, self.on_remove_cell_mapping),
            compat_QShortCut(pya.QKeySequence("Backspace"), 
                             tree, self.on_remove_cell_mapping)
        ]
    
    def add_row(self):
        # Add a new row with device_name pre-filled in col 0
        device_name = 'SG13_LV_NMOS'
        table = self._widget.cell_map_tw
        row = table.rowCount
        table.blockSignals(True)
        table.insertRow(row)
        table.setItem(row, 0, self._make_data_item(device_name))
        self._set_cell_type_widget(row, CellType.PCELL.value)
        self._set_cell_map_library_widget(row, 'SG13_dev')
        self._set_cell_map_cell_widget(row, device_name.lower(), 'SG13_dev')
        table.setItem(row, 4, self._make_data_item('w=@w l=@l ng=@ng'))
        table.setItem(row, 5, self._make_data_item('@m'))
        table.blockSignals(False)
        table.selectRow(row)
        table.scrollToItem(table.item(row, 0))
       
        self._notify_cell_map_changed()
       
    def _parse_parameter_mapping(self, text: str) -> ParameterMapping:
        entries = {}
        for token in text.strip().split():
            if '=' in token:
                key, _, value = token.partition('=')
                entries[key.strip()] = value.strip()
        return ParameterMapping(entries=entries)
    
    def _format_parameter_mapping(self, pm: ParameterMapping) -> str:
        return ' '.join(f'{k}={v}' for k, v in pm.entries.items())
        
    def _parse_netlist_node_order(self, text: str) -> List[str]:
        return text.strip().split()
    
    def _format_netlist_node_order(self, order: List[str]) -> str:
        return ' '.join(order)
        
    def cell_map_from_ui(self, table_widget: pya.QTableWidget):
        entries = []
        for row in range(table_widget.rowCount):
            def cell_text(col):
                item = table_widget.item(row, col)
                return item.text if item is not None else ''
                
            # Read library from combo widget
            lib_cb = self._cell_map_lib_combos.get(row)
            lib_name = lib_cb.currentText.strip() if lib_cb else cell_text(2)
            
            # Read cell from combo widget (prefer data role for bare name)
            cell_cb = self._cell_map_cell_combos.get(row)
            if cell_cb is not None:
                idx = cell_cb.currentIndex
                cell_name = cell_cb.itemData(idx) if idx >= 0 and cell_cb.itemData(idx) else cell_cb.currentText.strip()
            else:
                cell_name = cell_text(3)
                
            entries.append(CellMapEntry(
                netlist_device      = cell_text(0),
                layout_cell_type    = CellType(self._get_cell_type_value(row)),
                layout_cell_library = lib_name,
                layout_cell         = cell_name,
                parameter_mapping   = self._parse_parameter_mapping(cell_text(4)),
                multiplier          = cell_text(5),
                netlist_node_order  = self._parse_netlist_node_order(cell_text(6)),
            ))
        return CellMap(entries=entries)        
    
    def on_add_cell_mapping(self):
        if Debugging.DEBUG:
            debug("TechCellMappingPage.on_add_cell_mapping")
            
        try:
            table = self._widget.cell_map_tw
            row = table.rowCount
            table.blockSignals(True)
            table.insertRow(row)
            # Col 0 - netlist device
            table.setItem(row, 0, self._make_placeholder_item('SG13_LV_NMOS'))
            # Col 1 - cell type
            self._set_cell_type_widget(row, CellType.PCELL.value)
            # Col 2 - library
            self._set_cell_map_library_widget(row, 'SG13_dev')
            # Col 3 - cell
            self._set_cell_map_cell_widget(row, 'nmos', 'SG13_dev')
            # Col 4 - parameters
            table.setItem(row, 4, self._make_placeholder_item('w=@w l=@l ng=@ng'))
            # Col 5 - multiplier
            table.setItem(row, 5, self._make_placeholder_item('@m'))
            # Col 6 - netlist node order
            table.setItem(row, 6, self._make_placeholder_item('d g s b'))
            table.blockSignals(False)
            table.selectRow(row)
    
            self._notify_cell_map_changed()
        except Exception as e:
            traceback.print_exc()
    
    def on_remove_cell_mapping(self):
        if Debugging.DEBUG:
            debug("TechCellMappingPage.on_remove_cell_mapping")

        try:
            selected_rows = sorted(set(index.row() for index in self._widget.cell_map_tw.selectedItems()), reverse=True)
            
            for row in selected_rows:
                self._cell_type_combos.pop(row, None)
                getattr(self, '_stashed_params', {}).pop(row, None)
                self._widget.cell_map_tw.removeRow(row)
            # Re-key remaining combos/params after row removal
            self._reindex_cell_type_combos()
            self._reindex_stashed_params()        
    
            self._notify_cell_map_changed()
        except Exception as e:
            traceback.print_exc()
    
    def on_cell_map_selection_changed(self):
        if Debugging.DEBUG:
            debug("TechCellMappingPage.on_cell_map_selection_changed")
        selected = self._widget.cell_map_tw.selectedItems()
        self._widget.cell_map_remove_pb.setEnabled(bool(selected))

    def on_save_cell_map(self):
        """Save the current Tech Cell Mapping table to a JSON file."""
        if Debugging.DEBUG:
            debug("TechCellMappingPage.on_save_cell_map")
    
        try:
            start_dir = FileSystemHelpers.least_recent_directory()
            suggested = str(Path(start_dir) / self._suggest_cell_map_filename())
    
            file_path_str = pya.QFileDialog.getSaveFileName(
                self,
                "Save Cell Mapping",
                suggested,
                "Cell Mapping files (*.json);;All Files (*)"
            )
            if not file_path_str:
                return
    
            file_path = Path(file_path_str)
            if file_path.suffix.lower() != '.json':
                file_path = file_path.with_suffix('.json')
    
            cell_map = self.cell_map_from_ui(self._widget.cell_map_tw)
            cell_map.save_json(file_path)
            FileSystemHelpers.set_least_recent_directory(file_path.parent)
        except Exception as e:
            qmessagebox_critical('Error', "Failed to save cell mapping", f"<pre>{e}</pre>")
            traceback.print_exc()
    
    def on_load_cell_map(self):
        """Load a Tech Cell Mapping table from a JSON file."""
        if Debugging.DEBUG:
            debug("TechCellMappingPage.on_load_cell_map")
    
        try:
            start_dir = FileSystemHelpers.least_recent_directory()
    
            file_path_str = pya.QFileDialog.getOpenFileName(
                self,
                "Load Cell Mapping",
                start_dir,
                "Cell Mapping files (*.json);;All Files (*)"
            )
            if not file_path_str:
                return
    
            file_path = Path(file_path_str)
            cell_map = CellMap.load_json(file_path)
            self._apply_cell_map_to_ui(cell_map)
            FileSystemHelpers.set_least_recent_directory(file_path.parent)
        except Exception as e:
            qmessagebox_critical('Error', "Failed to load cell mapping", f"<pre>{e}</pre>")
            traceback.print_exc()
    
    @staticmethod
    def _suggest_cell_map_filename() -> str:
        """Return a suggested cell-map filename like ``TECH_cell_mapping.json``."""
        try:
            view = pya.LayoutView.current()
            if view:
                ly = view.active_cellview().layout()
                tech_name = ly.technology().name
                if tech_name:
                    safe = "".join(c if c.isalnum() or c in "-_.()" else "_" for c in tech_name)
                    return f"{safe}_cell_mapping.json"
        except Exception:
            pass
        return "cell_mapping.json"
    
    def _apply_cell_map_to_ui(self, cell_map: CellMap):
        """Replace the current Tech Cell Mapping table contents with *cell_map*."""
        table = self._widget.cell_map_tw
    
        self._cell_type_combos.clear()
        self._cell_map_lib_combos.clear()
        self._cell_map_cell_combos.clear()
        self._stashed_params = {}
    
        table.blockSignals(True)
        table.setRowCount(0)
    
        for row, e in enumerate(cell_map.entries):
            table.insertRow(row)
            # Col 0 – Netlist Device
            table.setItem(row, 0, self._make_data_item(e.netlist_device))
            # Col 1 – Cell Type
            self._set_cell_type_widget(row, e.layout_cell_type.value)
            # Col 2 – Library
            self._set_cell_map_library_widget(row, e.layout_cell_library)
            # Col 3 – Cell
            self._set_cell_map_cell_widget(row, e.layout_cell, e.layout_cell_library)
            # Col 4 – Parameters
            table.setItem(row, 4, self._make_data_item(
                self._format_parameter_mapping(e.parameter_mapping)
            ))
            # Col 5 - Multiplier
            table.setItem(row, 5, self._make_data_item(e.multiplier))
            # Col 6 - Netlist Node Order   
            self.setItem(row, 6, self._make_data_item(
                self._format_netlist_node_order(e.netlist_node_order)
            ))
    
        table.blockSignals(False)
        self._notify_cell_map_changed()

    def _reindex_cell_type_combos(self):
        """Rebuild the row→combo dict after rows are deleted."""
        new_type = {}
        new_lib = {}
        new_cell = {}
        for row in range(self._widget.cell_map_tw.rowCount):
            cb = self._widget.cell_map_tw.cellWidget(row, 1)
            if cb is not None:
                new_type[row] = cb
            cb = self._widget.cell_map_tw.cellWidget(row, 2)
            if cb is not None:
                new_lib[row] = cb
            cb = self._widget.cell_map_tw.cellWidget(row, 3)
            if cb is not None:
                new_cell[row] = cb
        self._cell_type_combos = new_type
        self._cell_map_lib_combos = new_lib
        self._cell_map_cell_combos = new_cell
        
    

    def _make_cell_type_combo(self, current_value: str = None) -> pya.QComboBox:
        """Create a QComboBox for CellType, optionally pre-selecting a value."""
        cb = pya.QComboBox()
        for ct in CellType:
            cb.addItem(ct.ui_label, ct.value)
        if current_value:
            for i in range(cb.count):
                if cb.itemData(i) == current_value or cb.itemText(i) == current_value:
                    cb.setCurrentIndex(i)
                    break
        return cb
    
    def _set_cell_map_library_widget(self, row: int, value: str):
        """Place a library QComboBox in column 2 of the given row."""
        cb = pya.QComboBox()
        cb.setEditable(True)
        cb.addItem("")
        for name in self.library_helper.get_library_names():
            cb.addItem(name)
        idx = cb.findText(value)
        if idx >= 0:
            cb.setCurrentIndex(idx)
        else:
            cb.setEditText(value)
        self._cell_map_lib_combos[row] = cb
        self._widget.cell_map_tw.setCellWidget(row, 2, cb)
        
        cb.currentTextChanged.connect(lambda text, r=row: self._on_cell_map_library_changed(r, text))
        cb.currentTextChanged.connect(lambda text, r=row: self._validate_cell_map_row(r))
    
    def _set_cell_map_cell_widget(self, row: int, value: str, lib_name: str = ''):
        """Place a cell QComboBox in column 3 of the given row."""
        cb = pya.QComboBox()
        cb.setEditable(True)
        self._populate_cell_map_cell_combo(cb, lib_name)
        idx = cb.findData(value)
        if idx >= 0:
            cb.setCurrentIndex(idx)
        else:
            cb.setEditText(value)
        self._cell_map_cell_combos[row] = cb
        self._widget.cell_map_tw.setCellWidget(row, 3, cb)
        # When cell selection changes, auto-switch the cell type combo
        cb.currentIndexChanged.connect(lambda _idx, r=row: self._on_cell_map_cell_changed(r))
        cb.currentIndexChanged.connect(lambda _idx, r=row: self._validate_cell_map_row(r))
        cb.currentTextChanged.connect(lambda text, r=row: self._validate_cell_map_row(r))
        # Initial validation (deferred so both combos are installed)
        EventLoop.defer(lambda r=row: self._validate_cell_map_row(r))
    
    def _populate_cell_map_cell_combo(self, cb: pya.QComboBox, lib_name: str):
        """Fill cell combo with 'P name' / 'S name' entries, storing bare name as data."""
        cb.blockSignals(True)
        cb.clear()
        cb.addItem("", "")
        for name, is_pcell in self.library_helper.get_library_cell_names_with_type(lib_name):
            prefix = "Ⓟ" if is_pcell else "Ⓢ"
            cb.addItem(f"{prefix} {name}", name)
        cb.blockSignals(False)
    
    def _on_cell_map_library_changed(self, row: int, lib_name: str):
        """Repopulate the cell combo and reset selection when the library combo changes."""
        cell_cb = self._cell_map_cell_combos.get(row)
        if cell_cb is None:
            return
        self._populate_cell_map_cell_combo(cell_cb, lib_name)
        # Reset cell to empty
        cell_cb.setCurrentIndex(0)
        cell_cb.setEditText('')
    
    def _on_cell_map_cell_changed(self, row: int):
        """Auto-switch the CellType combo when a cell is selected from the dropdown."""
        cell_cb = self._cell_map_cell_combos.get(row)
        if cell_cb is None:
            return
        idx = cell_cb.currentIndex
        if idx < 0:
            return
        cell_name = cell_cb.itemData(idx)
        if not cell_name:
            return
        
        # Determine if this cell is a PCell
        lib_cb = self._cell_map_lib_combos.get(row)
        if lib_cb is None:
            return
        lib_name = lib_cb.currentText.strip()
        if not lib_name:
            return
        
        cells_with_type = self.library_helper.get_library_cell_names_with_type(lib_name)
        is_pcell = False
        for name, pcell_flag in cells_with_type:
            if name == cell_name:
                is_pcell = pcell_flag
                break
        
        # Auto-switch CellType combo (suppress the clear from _on_cell_type_changed)
        type_cb = self._cell_type_combos.get(row)
        if type_cb is None:
            return
        target_type = CellType.PCELL.value if is_pcell else CellType.STATIC_CELL.value
        self._auto_switching_cell_type = True
        try:
            for i in range(type_cb.count):
                if type_cb.itemData(i) == target_type:
                    type_cb.setCurrentIndex(i)
                    break
        finally:
            self._auto_switching_cell_type = False
            
        self._notify_cell_map_changed()
        
    def _validate_cell_map_row(self, row: int):
        """Set red background on cell map library/cell combos if their value is invalid."""
        lib_cb = self._cell_map_lib_combos.get(row)
        cell_cb = self._cell_map_cell_combos.get(row)
        if lib_cb is None or cell_cb is None:
            return
        self.validate_lib_cell_combo(lib_cb, cell_cb, library_helper=self.library_helper, prefer_item_data=True)
    
    def _set_cell_type_widget(self, row: int, value: str):
        """Place a QComboBox in column 1 of the given row."""
        cb = self._make_cell_type_combo(value)
        self._cell_type_combos[row] = cb  # prevent GC
        self._widget.cell_map_tw.setCellWidget(row, 1, cb)
        cb.currentIndexChanged.connect(lambda _idx, r=row: self._on_cell_type_changed(r))
        self._update_param_cell_state(row, value)
    
    def _on_cell_type_changed(self, row: int):
        """Called when the CellType combo in *row* changes."""
        value = self._get_cell_type_value(row)
        self._update_param_cell_state(row, value)
            
        # Clear the cell combo only when the user manually switches cell type
        if not getattr(self, '_auto_switching_cell_type', False):
            cell_cb = self._cell_map_cell_combos.get(row)
            if cell_cb is not None:
                cell_cb.setCurrentIndex(0)
                cell_cb.setEditText('')
            self._validate_cell_map_row(row)
    
    def _update_param_cell_state(self, row: int, cell_type_value: str):
        """Enable/disable the parameter cell (col 4) based on cell type."""
        table = self._widget.cell_map_tw
        item = table.item(row, 4)
        if item is None:
            item = self._make_data_item('')
            table.setItem(row, 4, item)
    
        is_static = (cell_type_value == CellType.STATIC_CELL.value)
    
        if is_static:
            # Save current text so we can restore if user switches back
            self._stashed_params = getattr(self, '_stashed_params', {})
            current = item.text.strip()
            if current:
                self._stashed_params[row] = current
            item.setText('')
            item.setFlags(pya.Qt_QFlags_ItemFlag(int(item.flags) & ~int(pya.Qt.ItemIsEditable)))
            item.setForeground(pya.QBrush(pya.QColor(160, 160, 160)))
            item.setToolTip('Static cells do not have parameters')
        else:
            # Restore stashed params if available
            stashed = getattr(self, '_stashed_params', {}).pop(row, None)
            if stashed and not item.text.strip():
                item.setText(stashed)
            item.setFlags(pya.Qt_QFlags_ItemFlag(int(item.flags) | int(pya.Qt.ItemIsEditable)))
            item.setForeground(pya.QBrush(pya.QColor(0, 0, 0)))
            item.setToolTip('')
    
    def _get_cell_type_value(self, row: int) -> str:
        """Read the current CellType value from the combo in column 1."""
        cb = self._widget.cell_map_tw.cellWidget(row, 1)
        if cb is not None:
            return cb.itemData(cb.currentIndex)
        # Fallback to item text
        item = self._widget.cell_map_tw.item(row, 1)
        return item.text if item else CellType.STATIC_CELL.value
        
    def _on_cell_map_double_clicked(self, row: int, col: int):
        """Show an explanation when double-clicking a disabled parameter cell."""
        if col != 4:
            return
        cell_type_value = self._get_cell_type_value(row)
        if cell_type_value == CellType.STATIC_CELL.value:
            pya.QMessageBox.information(
                self,
                'Parameters Not Available',
                'Static cells are placed as fixed layout references and do not '
                'accept parameters.\n\n'
                'To use parameterised placement, change the cell type to "PCell".'
            )    
            
    def _make_placeholder_item(self, text: str) -> pya.QTableWidgetItem:
        item = pya.QTableWidgetItem(text)
        item.setForeground(pya.QBrush(pya.QColor(160, 160, 160)))  # grey
        item.setData(_PLACEHOLDER_ROLE, True)   # tag as placeholder
        return item
    
    def _make_data_item(self, text: str) -> pya.QTableWidgetItem:
        item = pya.QTableWidgetItem(text)
        item.setData(_PLACEHOLDER_ROLE, False)   # not a placeholder
        return item

    def _on_item_changed(self, item: pya.QTableWidgetItem):
        if Debugging.DEBUG:
            debug("TechCellMappingPage._on_item_changed")
            
        try:
            is_placeholder = item.data(_PLACEHOLDER_ROLE)
            if is_placeholder and item.text.strip() != '':
                item.setForeground(pya.QBrush(pya.QColor(0, 0, 0)))
                item.setData(_PLACEHOLDER_ROLE, False)
        except Exception as e:
            traceback.print_exc()
            
        self._notify_cell_map_changed()
        
    def _notify_cell_map_changed(self):
        """Call all registered on_cell_map_changed observers."""
        for cb in self.on_cell_map_changed:
            try:
                cb()
            except Exception:
                traceback.print_exc()
                
    def _reindex_stashed_params(self):
        """Rebuild row keys in _stashed_params after row deletion."""
        if not hasattr(self, '_stashed_params'):
            return
        # The stashed entries with old row keys are now invalid;
        # we don't have a reliable way to remap, so just clear.
        # (Stash is only a convenience for undo within same session.)
        self._stashed_params.clear()
                
