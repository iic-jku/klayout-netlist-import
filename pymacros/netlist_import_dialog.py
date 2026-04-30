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

import pya

from klayout_plugin_utils.debugging import debug, Debugging
from klayout_plugin_utils.file_selector_widget import FileSelectorWidget
from klayout_plugin_utils.file_system_helpers import FileSystemHelpers
from klayout_plugin_utils.lru_file_helper import LRUFileHelper
from klayout_plugin_utils.qt_helpers import (
    compat_QShortCut,
    compat_QTreeWidgetItem_setBackground,
    qmessagebox_critical
)

from netlist_import_config import *
from previous_ui_settings import PreviousUISettings

#--------------------------------------------------------------------------------

path_containing_this_script = os.path.realpath(os.path.join(os.path.dirname(__file__)))
    
# Config key used to persist the runset LRU list for this specific plugin.
_RUNSET_LRU_CONFIG_KEY = "import_netlist.lru_runsets"
_PLACEHOLDER_ROLE = int(pya.Qt.UserRole)

#--------------------------------------------------------------------------------

class NetlistImportDialog(pya.QDialog):
    def __init__(self, config: NetlistImportConfig, parent=None):
        super().__init__(parent)
        
        # LRU helper – reusable; the config key is plugin-specific
        self._lru = LRUFileHelper(config_key=_RUNSET_LRU_CONFIG_KEY, max_entries=15)
        
        self.setWindowTitle('Netlist Import')
        
        loader = pya.QUiLoader()
        ui_path = os.path.join(path_containing_this_script, "NetlistImportConfigPage.ui")
        ui_file = pya.QFile(ui_path)
        try:
            ui_file.open(pya.QFile.ReadOnly)
            self.page = loader.load(ui_file, self)
        finally:
            ui_file.close()

        self.page.cell_map_add_pb.icon = pya.QIcon(':add_16px')
        self.page.cell_map_remove_pb.icon = pya.QIcon(':del_16px')

        for pb in (self.page.cell_map_add_pb, self.page.cell_map_remove_pb):
            pb.text = ''
            pb.setFixedSize(40, 32)
        
        self.page.cell_map_add_pb.clicked.connect(self.on_add_cell_mapping)
        self.page.cell_map_remove_pb.clicked.connect(self.on_remove_cell_mapping)
        
        header = self.page.cell_map_tw.horizontalHeader
        header.setSectionResizeMode(0, pya.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, pya.QHeaderView.Fixed)
        header.setSectionResizeMode(2, pya.QHeaderView.Fixed)
        header.setSectionResizeMode(3, pya.QHeaderView.Stretch)
        self.page.cell_map_tw.setColumnWidth(3, 80)
        header.setStretchLastSection(False)      
        
        self.page.cell_map_tw.setSelectionBehavior(pya.QAbstractItemView.SelectRows)
        self.page.cell_map_tw.setSelectionMode(pya.QAbstractItemView.ExtendedSelection)
        
        self.page.cell_map_tw.itemSelectionChanged.connect(self.on_cell_map_selection_changed)
        self.page.cell_map_tw.currentItemChanged.connect(self.on_cell_map_selection_changed)
        self.page.cell_map_tw.itemChanged.connect(self._on_item_changed)
        
        # NOTE: qt5 vs qt6 has different QShortCut ctor arguments,
        #       thus use our safety wrapper        
        self.shortcuts = [
            compat_QShortCut(pya.QKeySequence("Delete"), 
                             self.page.cell_map_tw, self.on_remove_cell_mapping),
            compat_QShortCut(pya.QKeySequence("Backspace"), 
                             self.page.cell_map_tw, self.on_remove_cell_mapping)
        ]
        
        self.bottom = pya.QHBoxLayout()
        self.saveButton = pya.QPushButton('Save Runset')
        self.loadButton = pya.QPushButton('Load Runset')
        self.lruButton = pya.QPushButton('Recent')
        self.lruButton.setToolTip('Recently used runsets')
        self.importButton = pya.QPushButton('Import')
        self.cancelButton = pya.QPushButton('Cancel')
                
        self.lruMenu = pya.QMenu(self)
        self.lruButton.setMenu(self.lruMenu)
        
        # Layout: [Save] [Load] [Recent…▼]  <stretch>  [Import] [Cancel]
        
        self.bottom.addWidget(self.saveButton)
        self.bottom.addWidget(self.loadButton)
        self.bottom.addWidget(self.lruButton)
        
        self.bottom.addStretch(1)
        
        self.bottom.addWidget(self.importButton)
        self.bottom.addWidget(self.cancelButton)
        
        layout = pya.QVBoxLayout(self)
        layout.addWidget(self.page)
        layout.addLayout(self.bottom)
        
        self.importButton.clicked.connect(self.on_import)
        self.cancelButton.clicked.connect(self.on_cancel)
        self.saveButton.clicked.connect(self.on_save_runset)
        self.loadButton.clicked.connect(self.on_load_runset)
        
        # Rebuild the LRU menu every time the button is about to show its popup
        self.lruMenu.aboutToShow.connect(self._rebuild_lru_menu)
        
        self.importButton.setDefault(True)
        self.importButton.setAutoDefault(True)
        self.cancelButton.setAutoDefault(False)
        self.saveButton.setAutoDefault(False)
        self.loadButton.setAutoDefault(False)
        self.lruButton.setAutoDefault(False)
        
        self.page.file_format_cob.clear()
        for f in NetlistFileFormat:
            self.page.file_format_cob.addItem(f.ui_label, f.value)
        
        self.page.hierarchy_mode_cob.clear()
        for h in HierarchyMode:
            self.page.hierarchy_mode_cob.addItem(h.ui_label, h.value)
        
        self.source_path_w = self._replace_with_file_selector(
            self.page.source_path_le,
            editable=True,
            file_dialog_title='Select Netlist File',
            file_types=[
                'SPICE Netlist (*.cdl *.cir *.spi *.spice)',
                'All Files (*)',
            ]
        )
            
        self.update_ui_from_config(config)
            
    def _find_parent_layout(self, widget: pya.QWidget):
        """Walk the parent widget's layout tree to find the layout directly containing widget."""
        def search(layout):
            for i in range(layout.count()):
                item = layout.itemAt(i)
                w = item.widget()
                if w and w is widget:
                    return layout, i
                sub = item.layout()
                if sub:
                    result = search(sub)
                    if result is not None:
                        return result
            return None
        return search(widget.parent.layout)
        
    def _replace_with_file_selector(self, 
                                    placeholder: pya.QWidget,
                                    **file_selector_kwargs) -> FileSelectorWidget:
        """Replace a placeholder widget with a FileSelectorWidget in-place."""
        result = self._find_parent_layout(placeholder)
        if result is None:
            raise RuntimeError(f"Could not find layout containing {placeholder}")
            
        parent_layout, idx = result
        
        widget = FileSelectorWidget(placeholder.parent, **file_selector_kwargs)
        widget.setSizePolicy(placeholder.sizePolicy)
        widget.setMinimumSize(placeholder.minimumSize)
        widget.setMaximumSize(placeholder.maximumSize)
        
        parent_layout.removeWidget(placeholder)
        placeholder.hide()
        parent_layout.insertWidget(idx, widget)
        widget.show()
        
        return widget
            
    def on_reset(self):
        if Debugging.DEBUG:
            debug("NetlistImportDialog.on_reset")
        
        try:
            config = NetlistImportConfig()
            self.update_ui_from_config(config)    
        except Exception as e:
            print("NetlistImportDialog.on_reset caught an exception", e)
            traceback.print_exc()
        
    def on_import(self):
        if Debugging.DEBUG:
            debug("NetlistImportConfigPage.on_ok")
        
        try:
            config = self.config_from_ui()
            PreviousUISettings.save(config)
            
            # importer.import()
            
        except Exception as e:
            print("NetlistImportDialog.on_ok caught an exception", e)
            traceback.print_exc()
            
        self.accept()
        
    def on_cancel(self):
        if Debugging.DEBUG:
            debug("NetlistImportDialog.on_cancel")
        self.reject()
    
    def cell_map_from_ui(self, table_widget: QTableWidget):
        entries = []
        for row in range(table_widget.rowCount):
            def cell(col):
                item = table_widget.item(row, col)
                return item.text if item is not None else ''
            entries.append(CellMapEntry(
                netlist_device    = cell(0),
                target            = cell(1),
                target_type       = CellType(cell(2)) if cell(2) else CellType.STATIC_CELL,
                parameter_mapping = ParameterMapping(entries={}) # TODO: parse cell(3)
            ))
        return CellMap(entries=entries)        
    
    def config_from_ui(self) -> NetlistImportConfig:
        return NetlistImportConfig(
            source_path = Path(self.page.source_path_w.path),
            file_format = NetlistFileFormat(self.page.file_format_cob.currentData()),
            hierarchy_mode = HierarchyMode(self.page.hierarchy_mode_cob.currentData()),
            cell_map = self.cell_map_from_ui(self.page.cell_map_tw)
        )
    
    def update_ui_from_config(self, config: NetlistImportConfig):
        if Debugging.DEBUG:
            debug("NetlistImportDialog.update_ui_from_config")
        
        self.source_path_w.path = str(config.source_path) if config.source_path else ''
        
        idx = self.page.file_format_cob.findData(config.file_format.value)
        if idx >= 0:
            self.page.file_format_cob.setCurrentIndex(idx)

        idx = self.page.hierarchy_mode_cob.findData(config.hierarchy_mode.value)
        if idx >= 0:
            self.page.hierarchy_mode_cob.setCurrentIndex(idx)
        
        self.page.cell_map_tw.blockSignals(True)
        self.page.cell_map_tw.setRowCount(0)
        
        for row, e in enumerate(config.cell_map.entries):
            self.page.cell_map_tw.insertRow(row)
            cells = [
                e.netlist_device,
                e.target,
                str(e.target_type),
                str(e.parameter_mapping)
            ]
            for col, text in enumerate(cells):
                self.page.cell_map_tw.setItem(row, col, self._make_data_item(text))
                
        self.page.cell_map_tw.blockSignals(False)
    
    def _rebuild_lru_menu(self):
        """Populate (or refresh) the LRU popup menu."""
        self.lruMenu.clear()
        entries = self._lru.entries()

        if entries:
            for path in entries:
                # Use the file name as the label, full path as tooltip
                action = self.lruMenu.addAction(path.name)
                action.setToolTip(str(path))
                # Capture path in default-arg to avoid late-binding issues
                action.triggered.connect(lambda checked=False, p=path: self._load_runset_from_path(p))

            self.lruMenu.addSeparator()

        clear_action = self.lruMenu.addAction('Clear List')
        clear_action.triggered.connect(self.on_clear_lru)

    def on_clear_lru(self):
        self._lru.clear()

    def _load_runset_from_path(self, path: Path):
        """Load a runset JSON from *path* and apply it to the UI."""
        try:
            settings = NetlistImportConfig.load_json(path)
            self.update_ui_from_settings(settings)
            self._lru.push(path)
        except Exception as e:
            qmessagebox_critical(
                'Error',
                f"Failed to load runset",
                f"Caught exception: <pre>{e}</pre>"
            )
            traceback.print_exc()
    
    def on_save_runset(self):
        if Debugging.DEBUG:
            debug("NetlistImportDialog.on_save_runset")

        try:
            lru_entries = self._lru.entries()
            start_dir = str(lru_entries[0].parent) if lru_entries else FileSystemHelpers.least_recent_directory()

            suggested = str(Path(start_dir) / self._suggest_runset_filename())

            file_path_str = pya.QFileDialog.getSaveFileName(
                self,
                "Save Runset",
                suggested,
                "Runset files (*.json);;All Files (*)"
            )
            if not file_path_str:
                return

            file_path = Path(file_path_str)
            if file_path.suffix.lower() != '.json':
                file_path = file_path.with_suffix('.json')

            settings = self.settings_from_ui()
            
            settings.save_json(file_path)

            self._lru.push(file_path)
            FileSystemHelpers.set_least_recent_directory(file_path.parent)
        except Exception as e:
            qmessagebox_critical('Error', "Failed to save runset", f"Caught exception: <pre>{e}</pre>")
            traceback.print_exc()
        
    def on_load_runset(self):
        if Debugging.DEBUG:
            debug("NetlistImportDialog.on_load_runset")

        try:
            lru_entries = self._lru.entries()
            start_dir = str(lru_entries[0].parent) if lru_entries else FileSystemHelpers.least_recent_directory()

            file_path_str = pya.QFileDialog.getOpenFileName(
                self,
                "Load Runset",
                start_dir,
                "Runset files (*.json);;All Files (*)"
            )
            if not file_path_str:
                return

            file_path = Path(file_path_str)
            self._load_runset_from_path(file_path)
            FileSystemHelpers.set_least_recent_directory(file_path.parent)
        except Exception as e:
            qmessagebox_critical('Error', "Failed to load runset", f"Caught exception: <pre>{e}</pre>")
            traceback.print_exc()
    
    def on_browse_custom_folder(self):
        if Debugging.DEBUG:
            debug("NetlistImportDialog.on_browse_custom_folder")

        folder = pya.QFileDialog.getExistingDirectory(
            self,                   # parent (dialog stays on top)
            "Select Backup Folder", # title
            "",                     # starting dir ("" = default to last used / home)
            pya.QFileDialog.ShowDirsOnly | pya.QFileDialog.DontResolveSymlinks
        )

        if folder:
            self.page.folder_path_le.setText(folder)
    
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
            debug("NetlistImportDialog._on_item_changed")
            
        try:
            is_placeholder = item.data(_PLACEHOLDER_ROLE)
            if is_placeholder and item.text.strip() != '':
                item.setForeground(pya.QBrush(pya.QColor(0, 0, 0)))
                item.setData(_PLACEHOLDER_ROLE, False)
        except Exception as e:
            traceback.print_exc()
        
    def on_add_cell_mapping(self):
        if Debugging.DEBUG:
            debug("NetlistImportDialog.on_add_cell_mapping")
            
        try:
            row = self.page.cell_map_tw.rowCount
            self.page.cell_map_tw.blockSignals(True)
            self.page.cell_map_tw.insertRow(row)
            for col, hint in enumerate(['X?M', 'nmos', 'pcell', 'w=@w l=@l']):
                self.page.cell_map_tw.setItem(row, col, self._make_placeholder_item(hint))
            self.page.cell_map_tw.blockSignals(False)
            self.page.cell_map_tw.selectRow(row)
        except Exception as e:
            traceback.print_exc()
    
    def on_remove_cell_mapping(self):
        if Debugging.DEBUG:
            debug("NetlistImportDialog.on_remove_cell_mapping")

        try:
            selected_rows = sorted(set(index.row() for index in self.page.cell_map_tw.selectedItems()), reverse=True)
            
            for row in selected_rows:
                self.page.cell_map_tw.removeRow(row)
        except Exception as e:
            traceback.print_exc()

    def on_cell_map_selection_changed(self):
        if Debugging.DEBUG:
            debug("NetlistImportDialog.on_cell_map_selection_changed")
        selected = self.page.cell_map_tw.selectedItems()
        self.page.cell_map_remove_pb.setEnabled(bool(selected))


#--------------------------------------------------------------------------------

