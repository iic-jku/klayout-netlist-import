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

import pya

from klayout_plugin_utils.debugging import debug, Debugging
from klayout_plugin_utils.event_loop import EventLoop
from klayout_plugin_utils.file_selector_widget import FileSelectorWidget
from klayout_plugin_utils.file_system_helpers import FileSystemHelpers
from klayout_plugin_utils.library_helper import LibraryHelper
from klayout_plugin_utils.lru_file_helper import LRUFileHelper
from klayout_plugin_utils.netlist_parser import NetlistParser, Netlist, NetlistError, NetlistCell, DeviceInstance
from klayout_plugin_utils.qt_helpers import (
    compat_QShortCut,
    compat_QTreeWidgetItem_setBackground,
    qmessagebox_critical
)
from klayout_plugin_utils.ui_loader import load_ui

from layout_page import LayoutPage
from netlist_import_config import *
from netlist_importer import NetlistImporter
from netlist_source_page import NetlistSourcePage
from previous_netlist_import_ui_settings import PreviousUISettings
from tech_cell_mapping_page import TechCellMappingPage

#--------------------------------------------------------------------------------

path_containing_this_script = os.path.realpath(os.path.join(os.path.dirname(__file__)))
    
# Config key used to persist the runset LRU list for this specific plugin.
_RUNSET_LRU_CONFIG_KEY = "import_netlist.lru_runsets"

# Item-data roles
_PLACEHOLDER_ROLE = int(pya.Qt.UserRole)

#--------------------------------------------------------------------------------

class NetlistImportDialog(pya.QDialog):
    def __init__(self, config: NetlistImportConfig, parent=None):
        super().__init__(parent)
        
        # LRU helper – reusable; the config key is plugin-specific
        self._lru = LRUFileHelper(config_key=_RUNSET_LRU_CONFIG_KEY, max_entries=15)
        
        self.setWindowTitle('Netlist Import')
        
        # Load the form shell
        self.form = load_ui('NetlistImportConfig_Form.ui', self)
        
        self._setup_pages()
        self._setup_tree()
        self._setup_buttons()
        
        layout = pya.QVBoxLayout(self)
        layout.addWidget(self.form)
        layout.addLayout(self.bottom)
        
        self.update_ui_from_config(config)

    @property
    def layout(self) -> pya.Layout:
        return pya.CellView.active().layout()
        
    @property
    def tech(self) -> pya.Technology:
        return self.layout.technology()

    def _setup_pages(self):
        # Remove the placeholder pages in the .ui
        stack = self.form.pages_stack
        while stack.count > 0:
            w = stack.widget(0)
            stack.removeWidget(w)
        
        library_helper = LibraryHelper(self.tech)
        
        # Load each page and insert as a page
        self.page_cell_map = TechCellMappingPage(self.tech, library_helper)
        
        self.page_netlist = NetlistSourcePage(
            library_helper=library_helper,
            on_goto_tech_cell_mapping=self._on_goto_tech_cell_mapping,
            on_add_tech_cell_mapping=self._on_add_tech_cell_mapping,
            get_active_cell_map_fn=self.page_cell_map.config_from_ui,
        )
        
        self.page_cell_map.on_cell_map_changed += [self.page_netlist.refresh_tech_mapping_widgets]
        
        self.page_layout = LayoutPage()
        
        stack.insertWidget(0, self.page_netlist.widget())
        stack.insertWidget(1, self.page_cell_map.widget())
        stack.insertWidget(2, self.page_layout.widget())
        
        stack.setCurrentIndex(0)
        
    def _setup_tree(self):
        tree = self.form.items_tw
        tree.setHeaderHidden(True)
        
        items = [
            ('Netlist Source',    0),
            ('Tech Cell Mapping', 1),
            ('Layout',            2),
        ]
        
        for label, page_idx in items:
            item = pya.QTreeWidgetItem(tree)
            item.setText(0, label)
            item.setData(0, _PLACEHOLDER_ROLE, page_idx)
            
        tree.currentItemChanged.connect(self._on_tree_selection_changed)
        tree.setCurrentItem(tree.topLevelItem(0))

        hint_w = tree.sizeHintForColumn(0)
        min_w = hint_w + tree.indentation + 8
        tree.setMinimumWidth(min_w)
        tree.setMaximumWidth(min_w)
        tree.setSizePolicy(pya.QSizePolicy.Fixed, pya.QSizePolicy.Expanding)
        
        splitter = self.form.splitter
        splitter.setStretchFactor(0, 0)  # left (tree): don't stretch   
        splitter.setStretchFactor(1, 1)  # right (stack): absorbs all extra space        
        splitter.setCollapsible(0, False)  # ADD: prevent collapsing left panel
        splitter.setCollapsible(1, False)
        splitter.setSizes([min_w, splitter.width - min_w])
        
    def _on_tree_selection_changed(self, current, previous):
        if current is None:
            return
        page_idx = current.data(0, _PLACEHOLDER_ROLE)
        if page_idx is not None:
            self.form.pages_stack.setCurrentIndex(page_idx)
            
    def _setup_buttons(self):
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
            
            layout = pya.CellView.active().layout()
            lv = pya.LayoutView.current()
    
            importer = NetlistImporter(config, layout)
            
            lv.transaction("import netlist")
            try:
                importer.import_netlist_into_layout()
            finally:
                lv.commit()
            
            self.accept()
        except Exception as e:
            qmessagebox_critical('Error', "Import failed", f"<pre>{e}</pre>")
            traceback.print_exc()
        
    def on_cancel(self):
        if Debugging.DEBUG:
            debug("NetlistImportDialog.on_cancel")
        self.reject()
   
            
    def _on_goto_tech_cell_mapping(self, device_name: str):
        """Navigate to Tech Cell Mapping page and select the row for *device_name*."""
        # Switch to the Tech Cell Mapping page (index 1)
        self.form.pages_stack.setCurrentIndex(1)
        nav_item = self.form.items_tw.topLevelItem(1)
        if nav_item:
            self.form.items_tw.setCurrentItem(nav_item)
        
        self.page_cell_map.select_row_for_device(device_name)
        
    def _on_add_tech_cell_mapping(self, device_name: str):
        """Add a new Tech Cell Mapping row pre-filled for *device_name*, then navigate there."""
        # Navigate first so the user sees what was added
        self.form.pages_stack.setCurrentIndex(1)
        nav_item = self.form.items_tw.topLevelItem(1)
        if nav_item:
            self.form.items_tw.setCurrentItem(nav_item)
        
        self.page_cell_map.add_row()
         
    def config_from_ui(self) -> NetlistImportConfig:
        return NetlistImportConfig(
            netlist_source_config=self.page_netlist.config_from_ui(),
            tech_cell_map=self.page_cell_map.config_from_ui(),
            layout_config=self.page_layout.config_from_ui(),
        )

    def update_ui_from_config(self, config: NetlistImportConfig):
        if Debugging.DEBUG:
            debug("NetlistImportDialog.update_ui_from_config")
        self.page_netlist.update_ui_from_config(config.netlist_source_config)
        self.page_cell_map.update_ui_from_config(config.tech_cell_map)
        self.page_layout.update_ui_from_config(config.layout_config)
    
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
            config = NetlistImportConfig.load_json(path)
            self.update_ui_from_config(config)
            self._lru.push(path)
        except Exception as e:
            qmessagebox_critical(
                'Error',
                f"Failed to load runset",
                f"Caught exception: <pre>{e}</pre>"
            )
            traceback.print_exc()
    
    @staticmethod
    def _suggest_runset_filename() -> str:
        """Return a suggested runset filename like ``TOPCELL_netlist_import.json``.

        Falls back to ``netlist_import.json`` if no layout / cell is open.
        """
        try:
            view = pya.LayoutView.current()
            if view:
                cell_name = view.active_cellview().cell.name
                if cell_name:
                    # Sanitise: replace characters that are problematic in filenames
                    safe = "".join(c if c.isalnum() or c in "-_.()" else "_" for c in cell_name)
                    return f"{safe}_netlist_import.json"
        except Exception:
            pass
        return "netlist_import.json"
    
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

            config = self.config_from_ui()
            
            config.save_json(file_path)

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
        

#--------------------------------------------------------------------------------

