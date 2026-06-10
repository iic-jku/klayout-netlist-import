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
from klayout_plugin_utils.lru_file_helper import LRUFileHelper
from klayout_plugin_utils.qt_helpers import (
    compat_QShortCut,
    compat_QTreeWidgetItem_setBackground,
    qmessagebox_critical
)

from netlist_import_config import *
from netlist_importer import NetlistImporter
from netlist_parser import NetlistParser, Netlist, NetlistError, NetlistCell, DeviceInstance
from previous_netlist_import_ui_settings import PreviousUISettings

#--------------------------------------------------------------------------------

path_containing_this_script = os.path.realpath(os.path.join(os.path.dirname(__file__)))
    
# Config key used to persist the runset LRU list for this specific plugin.
_RUNSET_LRU_CONFIG_KEY = "import_netlist.lru_runsets"

_PLACEHOLDER_ROLE = int(pya.Qt.UserRole)

# Role used to tag items so they can be identified later.
_CELL_NAME_ROLE = int(pya.Qt.UserRole) + 1

# Roles for persisting static-cell library / cell name on instance items
_STATIC_LIBRARY_ROLE = int(pya.Qt.UserRole) + 2
_STATIC_CELL_ROLE    = int(pya.Qt.UserRole) + 3


#--------------------------------------------------------------------------------

class NetlistImportDialog(pya.QDialog):
    def __init__(self, config: NetlistImportConfig, parent=None):
        super().__init__(parent)
        
        # LRU helper – reusable; the config key is plugin-specific
        self._lru = LRUFileHelper(config_key=_RUNSET_LRU_CONFIG_KEY, max_entries=15)
        
        self.setWindowTitle('Netlist Import')
        
        # Load the form shell
        self.form = self._load_ui('NetlistImportConfig_Form.ui', self)
        
        self._setup_pages()
        self._setup_tree()
        self._setup_buttons()
        
        layout = pya.QVBoxLayout(self)
        layout.addWidget(self.form)
        layout.addLayout(self.bottom)
        
        self._setup_page_netlist()
        self._setup_page_cell_map()
        
        self.update_ui_from_config(config)

    @property
    def layout(self) -> pya.Layout:
        return pya.CellView.active().layout()
        
    @property
    def tech(self) -> pya.Technology:
        return self.layout.technology()

    def _load_ui(self, ui_filename: str, parent=None) -> pya.QWidget:
        """Load a .ui file and return its root widget."""
        loader = pya.QUiLoader()
        ui_path = os.path.join(path_containing_this_script, ui_filename)
        ui_file = pya.QFile(ui_path)
        try:
            ui_file.open(pya.QFile.ReadOnly)
            widget = loader.load(ui_file, parent)
        finally:
            ui_file.close()
        return widget

    def _setup_pages(self):
        # Remove the placeholder pages in the .ui
        stack = self.form.pages_stack
        while stack.count > 0:
            w = stack.widget(0)
            stack.removeWidget(w)
        
        # Load each page and insert as a page
        self.page_netlist = self._load_ui('NetlistImportConfig_NetlistPage.ui', stack)
        self.page_cell_map = self._load_ui('NetlistImportConfig_CellMapPage.ui', stack)
        self.page_layout = self._load_ui('NetlistImportConfig_LayoutPage.ui', stack)
        
        stack.insertWidget(0, self.page_netlist)
        stack.insertWidget(1, self.page_cell_map)
        stack.insertWidget(2, self.page_layout)
        
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
        
    def _setup_page_netlist(self):
        p = self.page_netlist
        p.file_format_cob.clear()
        for f in NetlistFileFormat:
            p.file_format_cob.addItem(f.ui_label, f.value)
        
        p.hierarchy_mode_cob.clear()
        for h in HierarchyMode:
            p.hierarchy_mode_cob.addItem(h.ui_label, h.value)
        
        self.source_path_w = self._replace_with_file_selector(
            p.source_path_le,
            editable=True,
            file_dialog_title='Select Netlist File',
            file_types=[
                'SPICE Netlist (*.cdl *.cir *.spi *.spice)',
                'All Files (*)',
            ]
        )
        
        self.source_path_w.on_path_changed += [
            self.on_netlist_path_changed
        ]
        
        p.reload_netlist_pb.clicked.connect(lambda: self.on_netlist_path_changed(self.source_path_w))
        
        self._setup_netlist_content_tree()
                    
    def _make_cell_import_setting_combo(self, current_value: str = None) -> pya.QComboBox:
        """Create a QComboBox for cell-level ImportMode."""
        choices = [
            ImportMode.NEW_CELL,
            ImportMode.EXTERNAL_STATIC_CELL,
            ImportMode.IGNORE,
        ]
        cb = pya.QComboBox()
        for s in choices:
            cb.addItem(s.ui_label, s.value)
        if current_value:
            for i in range(cb.count):
                if cb.itemData(i) == current_value:
                    cb.setCurrentIndex(i)
                    break
        return cb    
        
    def _make_instance_import_setting_combo(self, current_value: str = None) -> pya.QComboBox:
        """Create a QComboBox for instance-level ImportMode (Import Mode column).

        Offers Tech Cell Mapping, External Static Cell, and Ignore.
        """
        choices = [
            ImportMode.TECH_CELL_MAPPING,
            ImportMode.EXTERNAL_STATIC_CELL,
            ImportMode.IGNORE,
        ]
        cb = pya.QComboBox()
        for s in choices:
            cb.addItem(s.ui_label, s.value)
        if current_value:
            for i in range(cb.count):
                if cb.itemData(i) == current_value:
                    cb.setCurrentIndex(i)
                    break
        return cb
    
    
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
    
    def _setup_page_cell_map(self):
        p = self.page_cell_map
        
        p.tech_cell_mapping_gb.setTitle(f"Cell Mapping (Technology {self.tech.name})")
        
        p.cell_map_add_pb.icon = pya.QIcon(':add_16px')
        p.cell_map_remove_pb.icon = pya.QIcon(':del_16px')

        for pb in (p.cell_map_add_pb, p.cell_map_remove_pb):
            pb.text = ''
            pb.setFixedSize(40, 32)
        
        p.cell_map_add_pb.clicked.connect(self.on_add_cell_mapping)
        p.cell_map_remove_pb.clicked.connect(self.on_remove_cell_mapping)
        
        tree = p.cell_map_tw
        header = tree.horizontalHeader
        header.setSectionResizeMode(0, pya.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, pya.QHeaderView.Fixed)
        tree.setColumnWidth(1, 120)
        header.setSectionResizeMode(2, pya.QHeaderView.Fixed)
        tree.setColumnWidth(2, 200)
        header.setSectionResizeMode(3, pya.QHeaderView.Fixed)
        tree.setColumnWidth(3, 200)
        header.setStretchLastSection(True)
        
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
        
        self._cell_type_combos = {}
        self._cell_map_lib_combos = {}
        self._cell_map_cell_combos = {}
    
    def _set_cell_map_library_widget(self, row: int, value: str):
        """Place a library QComboBox in column 2 of the given row."""
        cb = pya.QComboBox()
        cb.setEditable(True)
        cb.addItem("")
        for name in self._get_library_names():
            cb.addItem(name)
        idx = cb.findText(value)
        if idx >= 0:
            cb.setCurrentIndex(idx)
        else:
            cb.setEditText(value)
        self._cell_map_lib_combos[row] = cb
        self.page_cell_map.cell_map_tw.setCellWidget(row, 2, cb)
        # When library changes, repopulate the cell combo in the same row
        cb.currentTextChanged.connect(lambda text, r=row: self._on_cell_map_library_changed(r, text))
    
    def _set_cell_map_cell_widget(self, row: int, value: str, lib_name: str = ''):
        """Place a cell QComboBox in column 3 of the given row."""
        cb = pya.QComboBox()
        cb.setEditable(True)
        cb.addItem("")
        for cname in self._get_library_cell_names(lib_name):
            cb.addItem(cname)
        idx = cb.findText(value)
        if idx >= 0:
            cb.setCurrentIndex(idx)
        else:
            cb.setEditText(value)
        self._cell_map_cell_combos[row] = cb
        self.page_cell_map.cell_map_tw.setCellWidget(row, 3, cb)
    
    def _on_cell_map_library_changed(self, row: int, lib_name: str):
        """Repopulate the cell combo when the library combo changes."""
        cell_cb = self._cell_map_cell_combos.get(row)
        if cell_cb is None:
            return
        prev_cell = cell_cb.currentText
        cell_cb.blockSignals(True)
        cell_cb.clear()
        cell_cb.addItem("")
        for cname in self._get_library_cell_names(lib_name):
            cell_cb.addItem(cname)
        idx = cell_cb.findText(prev_cell)
        if idx >= 0:
            cell_cb.setCurrentIndex(idx)
        else:
            cell_cb.setEditText(prev_cell)
        cell_cb.blockSignals(False)    
    
    def _set_cell_type_widget(self, row: int, value: str):
        """Place a QComboBox in column 1 of the given row."""
        cb = self._make_cell_type_combo(value)
        self._cell_type_combos[row] = cb  # prevent GC
        self.page_cell_map.cell_map_tw.setCellWidget(row, 1, cb)
        cb.currentIndexChanged.connect(lambda _idx, r=row: self._on_cell_type_changed(r))
        self._update_param_cell_state(row, value)
    
    def _on_cell_type_changed(self, row: int):
        """Called when the CellType combo in *row* changes."""
        value = self._get_cell_type_value(row)
        self._update_param_cell_state(row, value)
    
    def _update_param_cell_state(self, row: int, cell_type_value: str):
        """Enable/disable the parameter cell (col 4) based on cell type."""
        table = self.page_cell_map.cell_map_tw
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
        cb = self.page_cell_map.cell_map_tw.cellWidget(row, 1)
        if cb is not None:
            return cb.itemData(cb.currentIndex)
        # Fallback to item text
        item = self.page_cell_map.cell_map_tw.item(row, 1)
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
            
    def _replace_with_file_selector(self, 
                                    placeholder: pya.QWidget,
                                    **file_selector_kwargs) -> FileSelectorWidget:
        """Replace a placeholder widget with a FileSelectorWidget in-place."""
        
        widget = FileSelectorWidget(placeholder.parent, **file_selector_kwargs)
        widget.setSizePolicy(placeholder.sizePolicy)
        widget.setMinimumSize(placeholder.minimumSize)
        widget.setMaximumSize(placeholder.maximumSize)
        
        parent_layout = self.page_netlist.source_file_layout
        parent_layout.removeWidget(placeholder)
        placeholder.hide()
        parent_layout.insertWidget(1, widget)
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
   
    def populate_netlist_content_tree(
        self,
        tree: pya.QTreeWidget,
        netlist_path: str,
        cell_import_settings: List[CellImportSetting] = None,
    ):
        """Populate *tree* with the cells parsed from *netlist_path*.
    
        Parameters
        ----------
        tree:
            The QTreeWidget named ``netlist_content_tw`` loaded from the .ui file.
        netlist_path:
            Absolute path to the SPICE/CDL netlist file.
        cell_import_settings:
            Optional list of persisted per-cell import settings.
            When provided, combo boxes are restored to their saved values.
        """
        
        settings_map = {}
        if cell_import_settings:
            for cis in cell_import_settings:
                settings_map[cis.cell_name] = cis
        
        # ---- parse -------------------------------------------------------
        parser = NetlistParser()
        netlist = parser.parse(netlist_path)
     
        # ---- reset the tree ----------------------------------------------
        tree.clear()
        self._import_setting_combos.clear()
        self._import_settings_widgets.clear()
        tree.setHeaderHidden(False)     # columns defined in the .ui are kept
        
        for cell in netlist.cells:
            cis = settings_map.get(cell.name)
            self._add_cell_item(
                tree.invisibleRootItem(), cell,
                import_mode=cis.import_mode.value if cis else None,
                static_library=cis.static_library if cis else '',
                static_cell=cis.static_cell if cis else '',
                instance_settings=cis.instance_settings if cis else None,
            )
     
        # Resize all columns to fit their contents compactly.
        for col in range(tree.columnCount):
            tree.resizeColumnToContents(col)
        
        # Limit text-heavy columns to reasonable widths.
        if tree.columnWidth(2) > 150:
            tree.setColumnWidth(2, 150)
        if tree.columnWidth(3) > 150:
            tree.setColumnWidth(3, 150)
     
    # ---------------------------------------------------------------------------
    # Private helpers
    # ---------------------------------------------------------------------------
       
    def _add_cell_item(
        self,
        parent: pya.QTreeWidgetItem,
        cell: NetlistCell,
        import_mode: str = None,
        static_library: str = '',
        static_cell: str = '',
        instance_settings: List[InstanceImportSetting] = None,
    ) -> pya.QTreeWidgetItem:
        """Create one child row for *cell* under *parent* and return it.
     
        Column mapping
        ~~~~~~~~~~~~~~
        0  Reference        – subckt / cell name
        1  Device/Instances – instance count
        2  Ports/Nodes      – port list
        3  Parameters       – subckt default parameters
        4  Import Mode      – combo box (cell-level)
        5  Import Settings  – (unused at cell level)
        """
        
        tree = parent.treeWidget()
        item = pya.QTreeWidgetItem(parent)
     
        # Col 0 – cell name (used as the reference / subckt identifier)
        item.setText(0, cell.name)
        item.setData(0, _CELL_NAME_ROLE, cell.name)
     
        # Col 1 – total number of instances inside this cell
        item.setText(1, str(len(cell.instances)))
     
        # Col 2 – port list gives a quick parameter overview
        item.setText(2, " ".join(cell.ports) if cell.ports else "")
            
        # Col 3 – subckt-level default parameters (from .subckt line)
        if cell.substitute:
            params_str = " ".join(f"{k}={v}" for k, v in cell.substitute.items())
            item.setText(3, params_str)
        
        # Col 4 – Import Settings combo box
        effective_mode = import_mode or ImportMode.NEW_CELL.value
        cb = self._make_cell_import_setting_combo(effective_mode)
        self._import_setting_combos[id(item)] = cb  # prevent GC
        tree.setItemWidget(item, 4, cb)
        
        # Store static cell lib/cell in item data roles
        item.setData(0, _STATIC_LIBRARY_ROLE, static_library or '')
        item.setData(0, _STATIC_CELL_ROLE,    static_cell    or '')
        
        # Col 5 – Import Settings widget (mode-dependent, same as instances)
        self._refresh_cell_import_settings_widget(tree, item, effective_mode)
        
        # Rebuild col-5 widget whenever the mode combo changes
        cb.currentIndexChanged.connect(
            lambda _idx, t=tree, it=item:
                self._on_cell_mode_changed(t, it)
        )
        
        # Build instance lookup
        inst_map = {}
        if instance_settings:
            for sis in instance_settings:
                inst_map[sis.instance_name] = sis
    
        cell_map = self.config_from_ui().cell_map if hasattr(self, '_config') else None
        try:
            cell_map = self.config_from_ui().cell_map
        except Exception:
            cell_map = None
    
        for inst in cell.instances:
            sis = inst_map.get(inst.name)
            self._add_instance_item(
                item, inst,
                import_mode=sis.import_mode.value if sis else None,
                static_library=sis.static_library if sis and hasattr(sis, 'static_library') else '',
                static_cell=sis.static_cell if sis and hasattr(sis, 'static_cell') else '',
                cell_map=cell_map,
            )
     
        return item
    
    def _on_cell_mode_changed(self, tree, item):
        """Slot: rebuild the Import Settings widget when the cell mode combo changes."""
        cb = tree.itemWidget(item, 4)
        if cb is None:
            return
        mode = cb.itemData(cb.currentIndex)
        
        def _refresh():
            self._refresh_cell_import_settings_widget(tree, item, mode)
        
        EventLoop.defer(_refresh)
    
    def _refresh_cell_import_settings_widget(self, tree, item, mode):
        """Build and install the correct Import Settings widget in col 5 for a cell item."""
        if mode == ImportMode.EXTERNAL_STATIC_CELL.value:
            w = self._make_static_cell_widget(item, instance=False)
        else:
            w = pya.QWidget()
        old_item_widget = tree.itemWidget(item, 5)
        self._import_settings_widgets[id(item)] = w
        tree.setItemWidget(item, 5, w)
    
    def _add_instance_item(
        self,
        parent: pya.QTreeWidgetItem,
        inst: DeviceInstance,
        import_mode: str = None,
        static_library: str = '',
        static_cell: str = '',
        cell_map=None,
    ) -> pya.QTreeWidgetItem:
        """Create a child row for a device instance under a cell item.
    
        Column mapping
        ~~~~~~~~~~~~~~
        0  Reference        – instance name (e.g. M0, X1, R3)
        1  Device           – device/subckt name being instantiated
        2  Nodes            – connected nodes
        3  Parameters       – instance parameters (key=value)
        4  Import Mode      – combo box (Tech Mapping, Ignore, etc.)
        5  Import Settings  – dynamic widget depending on the Import Mode 
        """
        tree = parent.treeWidget()
        item = pya.QTreeWidgetItem(parent)
    
        # Col 0 – instance name
        item.setText(0, inst.name)
    
        # Col 1 – device name (subckt or primitive being instantiated)
        item.setText(1, inst.device_name or inst.number or "")
    
        # Col 2 – nodes
        item.setText(2, " ".join(inst.nodes) if inst.nodes else "")
    
        # Col 3 – parameters as key=value pairs
        if inst.parameters:
            params_str = " ".join(f"{k}={v}" for k, v in inst.parameters.items())
            item.setText(3, params_str)
    
        # Style instance rows slightly differently
        grey = pya.QBrush(pya.QColor(100, 100, 100))
        for col in range(tree.columnCount):
            item.setForeground(col, grey)
    
        # Col 4 – Import Mode combo box
        effective_mode = import_mode or ImportMode.TECH_CELL_MAPPING.value
        cb = self._make_instance_import_setting_combo(effective_mode)
        self._import_setting_combos[id(item)] = cb
        tree.setItemWidget(item, 4, cb)
    
        # Store static cell lib/cell in item data roles so they survive tree rebuilds
        item.setData(0, _STATIC_LIBRARY_ROLE, static_library or '')
        item.setData(0, _STATIC_CELL_ROLE,    static_cell    or '')
 
        # Col 5 – Import Settings widget (mode-dependent)
        self._refresh_import_settings_widget(tree, item, inst.device_name or '', effective_mode, cell_map)
 
        # Rebuild col-5 widget whenever the mode combo changes
        cb.currentIndexChanged.connect(
            lambda _idx, t=tree, it=item, dev=inst.device_name or '', cm=cell_map:
                self._on_instance_mode_changed(t, it, dev, cm)
        )
        
        return item
    
    def _on_instance_mode_changed(self, tree, item, device_name, cell_map):
        """Slot: rebuild the Import Settings widget when the mode combo changes."""
        cb = tree.itemWidget(item, 4)
        if cb is None:
            return
        mode = cb.itemData(cb.currentIndex)
        
        def _refresh():
            self._refresh_import_settings_widget(tree, item, device_name, mode, cell_map)
        
        EventLoop.defer(_refresh)
 
    def _refresh_import_settings_widget(self, tree, item, device_name, mode, cell_map):
        """Build and install the correct Import Settings widget in col 5."""
        
        old_widget = self._import_settings_widgets.get(id(item))
        
        w = self._make_import_settings_widget(item, device_name, mode, cell_map)
        if w is None:
            # Install an empty transparent widget so the column stays blank
            w = pya.QWidget()
        # Always store a reference (even None → use empty widget) to prevent GC
        self._import_settings_widgets[id(item)] = w
        tree.setItemWidget(item, 5, w)
 
    def _make_import_settings_widget(self, item, device_name, mode, cell_map):
        """Return the appropriate widget for col 5 depending on *mode*.
 
        TECH_CELL_MAPPING
            • No match in cell_map → red warning label + [Ignore] + [Add →] buttons
            • Match found          → lib/cell label + [→] navigate button
        EXTERNAL_STATIC_CELL
            • Library: [QLineEdit]  Cell: [QLineEdit]
        IGNORE / other
            → None (empty)
        """
        if mode == ImportMode.TECH_CELL_MAPPING.value:
            return self._make_tech_mapping_widget(item, device_name, cell_map, instance=True)
        elif mode == ImportMode.EXTERNAL_STATIC_CELL.value:
            return self._make_static_cell_widget(item, instance=True)
        else:
            return None
 
    @property
    def netlist_page_cell_button_height(self) -> int:
        return 30
 
    def _make_tech_mapping_widget(self, item, device_name, cell_map, instance=False):
        """Widget for TECH_CELL_MAPPING mode in col 5."""
        map_entry = cell_map.map_entry_for_device(device_name) if cell_map and device_name else None
 
        container = pya.QWidget()
        grey_style = "color: #646464;" if instance else ""
        layout = pya.QHBoxLayout(container)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(4)
 
        if map_entry is None:
            # ── No match: red warning ───────────────────────────────────────
            warn_lbl = pya.QLabel("⚠ No tech cell mapping")
            warn_lbl.setStyleSheet("color: #c0392b; font-weight: bold;")
            warn_lbl.setToolTip(
                f"Device '{device_name}' has no entry in the Tech Cell Mapping table."
            )
 
            ignore_btn = pya.QPushButton("Ignore")
            ignore_btn.setToolTip("Switch this instance to Ignore mode")
            ignore_btn.setFixedHeight(self.netlist_page_cell_button_height)
            ignore_btn.clicked.connect(
                lambda checked=False, it=item:
                    self._set_instance_mode(it, ImportMode.IGNORE.value)
            )
 
            add_btn = pya.QPushButton("Add ▶")
            add_btn.setToolTip(
                f"Add a new Tech Cell Mapping entry for '{device_name}' "
                f"and navigate to the Tech Cell Mapping page"
            )
            add_btn.setFixedHeight(self.netlist_page_cell_button_height)
            add_btn.clicked.connect(
                lambda checked=False, dev=device_name:
                    self._on_add_tech_cell_mapping(dev)
            )
 
            layout.addWidget(warn_lbl)
            layout.addWidget(ignore_btn)
            layout.addWidget(add_btn)
            layout.addStretch(1)
        else:
            # ── Match found: show lib / cell ────────────────────────────────
            info_lbl = pya.QLabel(f"{map_entry.layout_cell_library} / {map_entry.layout_cell}")
            if instance:
                info_lbl.setStyleSheet(grey_style)
            info_lbl.setToolTip(
                f"Library: {map_entry.layout_cell_library}\n"
                f"Cell:    {map_entry.layout_cell}\n"
                f"Type:    {map_entry.layout_cell_type.ui_label}"
            )
 
            goto_btn = pya.QPushButton("▶")
            goto_btn.setToolTip("Go to Tech Cell Mapping and select this entry")
            goto_btn.setFixedSize(28, self.netlist_page_cell_button_height)
            goto_btn.clicked.connect(
                lambda checked=False, dev=device_name:
                    self._on_goto_tech_cell_mapping(dev)
            )
 
            layout.addWidget(info_lbl)
            layout.addWidget(goto_btn)
            layout.addStretch(1)
 
        return container
 
    def _get_library_names(self) -> List[str]:
        """Return sorted list of all registered KLayout library names."""
        try:
            names = sorted([
                l.name()
                for l in [
                    pya.Library.library_by_id(i) 
                    for i in pya.Library.library_ids()
                ]
                if self.tech.name in l.technologies() \
                   or not l.technologies()
            ])
            
            if Debugging.DEBUG:
                debug(f"NetlistImportPluginFactory._get_library_names: {names}")
            return names
        except Exception as e:
            if Debugging.DEBUG:
                debug(f"NetlistImportPluginFactory._get_library_names FAILED: {e}")
            traceback.print_exc()
            return []
    
    def _get_library_cell_names(self, lib_name: str) -> List[str]:
        """Return sorted list of cell names in the given library."""
        try:
            lib = pya.Library.library_by_name(lib_name, self.tech.name)
            if lib is None:
                if Debugging.DEBUG:
                    debug(f"NetlistImportPluginFactory._get_library_cell_names: no lib '{lib_name}'")
                return []
            ly = lib.layout()
            names = sorted([c.name for c in ly.each_cell()])
            if Debugging.DEBUG:
                debug(f"NetlistImportPluginFactory._get_library_cell_names({lib_name}): {names[:10]}...")
            if not names:
                pass
            return names
        except Exception as e:
            print(f"DEBUG _get_library_cell_names FAILED: {e}")
            traceback.print_exc()
            return []
            
    def _validate_static_cell_combo(self, lib_cb: pya.QComboBox, cell_cb: pya.QComboBox):
        """Set red background on library/cell combos if their value is invalid."""
        lib_name = lib_cb.currentText.strip()
        cell_name = cell_cb.currentText.strip()
    
        lib_valid = bool(lib_name) and lib_name in self._get_library_names()
        cell_valid = False
        if lib_valid and cell_name:
            cell_valid = cell_name in self._get_library_cell_names(lib_name)
    
        red = "QComboBox { background-color: #ffcccc; }"
        ok  = ""
        lib_cb.setStyleSheet(red if not lib_valid else ok)
        cell_cb.setStyleSheet(red if not cell_valid else ok)

    def _make_static_cell_widget(self, item, instance=False):
        """Widget for EXTERNAL_STATIC_CELL mode in col 5: Library + Cell line edits."""
        # Restore previously stored values (survive mode switches)
        saved_lib  = item.data(0, _STATIC_LIBRARY_ROLE) or ''
        saved_cell = item.data(0, _STATIC_CELL_ROLE)    or ''
 
        container = pya.QWidget()
        grey_style = "color: #646464;" if instance else ""
        layout = pya.QHBoxLayout(container)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(4)
 
        lbl_lib = pya.QLabel("Library:")
        if instance:
            lbl_lib.setStyleSheet(grey_style)
        lib_cb  = pya.QComboBox()
        lib_cb.setEditable(True)
        lib_cb.setMinimumWidth(100)
        
        lbl_cell = pya.QLabel("Cell:")
        if instance:
            lbl_cell.setStyleSheet(grey_style)
        cell_cb  = pya.QComboBox()
        cell_cb.setEditable(True)
        cell_cb.setMinimumWidth(100)
        
        def _populate_cell_combo(lib_name: str):
            """Repopulate cell combo when library selection changes."""
            prev_cell = cell_cb.currentText
            cell_cb.blockSignals(True)
            cell_cb.clear()
            cell_cb.addItem("")
            for cname in self._get_library_cell_names(lib_name):
                cell_cb.addItem(cname)
            # Try to restore previous cell selection
            idx = cell_cb.findText(prev_cell)
            if idx >= 0:
                cell_cb.setCurrentIndex(idx)
            else:
                cell_cb.setEditText(prev_cell)
            cell_cb.blockSignals(False)

        lib_cb.blockSignals(True)
        lib_cb.addItem("")  # allow empty / manual entry
        for name in self._get_library_names():
            lib_cb.addItem(name)
        # Restore saved value
        idx = lib_cb.findText(saved_lib)
        if idx >= 0:
            lib_cb.setCurrentIndex(idx)
        else:
            lib_cb.setEditText(saved_lib)
        lib_cb.addItem("")
        lib_cb.blockSignals(False)
        
        # Initial population of cell combo
        cell_cb.blockSignals(True)
        _populate_cell_combo(saved_lib)
        # Restore saved cell
        idx = cell_cb.findText(saved_cell)
        if idx >= 0:
            cell_cb.setCurrentIndex(idx)
        else:
            cell_cb.setEditText(saved_cell)
        cell_cb.blockSignals(False)
    
        layout.addWidget(lbl_lib)
        layout.addWidget(lib_cb, 1)
        layout.addWidget(lbl_cell)
        layout.addWidget(cell_cb, 1)
                
        # When library changes, repopulate cells and persist
        def _on_lib_changed(text, it=item):
            it.setData(0, _STATIC_LIBRARY_ROLE, text)
            _populate_cell_combo(text)
            self._validate_static_cell_combo(lib_cb, cell_cb)
        
        lib_cb.currentTextChanged.connect(_on_lib_changed)
        lib_cb.currentIndexChanged.connect(
            lambda _idx: _on_lib_changed(lib_cb.currentText)
        )
        
        def _on_cell_changed(text, it=item):
            it.setData(0, _STATIC_CELL_ROLE, text)
            self._validate_static_cell_combo(lib_cb, cell_cb)
        
        cell_cb.currentTextChanged.connect(_on_cell_changed)        
        
        # Initial validation
        self._validate_static_cell_combo(lib_cb, cell_cb)
        
        container.setMinimumWidth(200)
        
        # Keep Python references to child widgets alive to prevent pya GC crash
        key = id(item)
        self._import_settings_widgets[('children', key)] = [lbl_lib, lib_cb, lbl_cell, cell_cb, layout]
        
        return container
 
    def _set_instance_mode(self, item, mode_value: str):
        """Programmatically switch the Import Mode combo for *item* to *mode_value*."""
        tree = item.treeWidget()
        if tree is None:
            return
        cb = tree.itemWidget(item, 4)
        if cb is None:
            return
            
        def _apply():
            for i in range(cb.count):
                if cb.itemData(i) == mode_value:
                    cb.setCurrentIndex(i)
                    break
        EventLoop.defer(_apply)
 
    def _on_goto_tech_cell_mapping(self, device_name: str):
        """Navigate to Tech Cell Mapping page and select the row for *device_name*."""
        # Switch to the Tech Cell Mapping page (index 1)
        self.form.pages_stack.setCurrentIndex(1)
        nav_item = self.form.items_tw.topLevelItem(1)
        if nav_item:
            self.form.items_tw.setCurrentItem(nav_item)
 
        # Select the matching row in the cell map table
        table = self.page_cell_map.cell_map_tw
        for row in range(table.rowCount):
            item = table.item(row, 0)
            if item and item.text.lower() == device_name.lower():
                table.selectRow(row)
                table.scrollToItem(item)
                break
 
    def _on_add_tech_cell_mapping(self, device_name: str):
        """Add a new Tech Cell Mapping row pre-filled for *device_name*, then navigate there."""
        # Navigate first so the user sees what was added
        self.form.pages_stack.setCurrentIndex(1)
        nav_item = self.form.items_tw.topLevelItem(1)
        if nav_item:
            self.form.items_tw.setCurrentItem(nav_item)
 
        # Add a new row with device_name pre-filled in col 0
        table = self.page_cell_map.cell_map_tw
        row = table.rowCount
        table.blockSignals(True)
        table.insertRow(row)
        table.setItem(row, 0, self._make_data_item(device_name))
        self._set_cell_type_widget(row, CellType.PCELL.value)
        self._set_cell_map_library_widget(row, 'SG13_dev')
        self._set_cell_map_cell_widget(row, device_name.lower(), 'SG13_dev')
        table.setItem(row, 4, self._make_data_item('w=@w l=@l ng=@ng m=@m'))
        table.blockSignals(False)
        table.selectRow(row)
        table.scrollToItem(table.item(row, 0))
       
    def on_netlist_path_changed(self, widget: FileSelectorWidget):
        try:
            new_path = widget.path
            if Debugging.DEBUG:
                debug("NetlistImportDialog.on_netlist_path_changed: new path: {new_path}")
            self._reload_netlist_tree(new_path)
        except Exception as e:
            traceback.print_exc()
    
    def _setup_netlist_content_tree(self):
        """Wire up the netlist-content tree on the Netlist page."""
        tree = self.page_netlist.netlist_content_tw

        # Remove placeholder columns added by Qt Designer and replace with meaningful headers
        headers = [
            "Reference", 
            "Device / Instances", 
            "Ports / Nodes",
            "Parameters",
            "Import Mode",
            "Import Settings",
        ]
        tree.setHeaderLabels(headers)
        tree.setColumnCount(6)

        header = tree.header
        header.setSectionResizeMode(0, pya.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, pya.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, pya.QHeaderView.Interactive)
        header.setSectionResizeMode(3, pya.QHeaderView.Interactive)
        header.setSectionResizeMode(4, pya.QHeaderView.ResizeToContents)        
        header.setStretchLastSection(True)

        tree.setSelectionMode(pya.QAbstractItemView.ExtendedSelection)
        tree.setAlternatingRowColors(True)
        
        # Resize all columns to fit their contents compactly.
        for col in range(tree.columnCount):
            tree.resizeColumnToContents(col)
            
        self._import_setting_combos = {}
        self._import_settings_widgets = {}  # col-5 widgets keyed by id(item)

        # Make rows more compact
        tree.setStyleSheet("QTreeWidget::item { padding: 0px; margin: 0px; }")
        tree.setUniformRowHeights(True)
        
        # Reduce indentation for nested items
        tree.setIndentation(16)  # default is ~20      
        
        tree.setSizePolicy(pya.QSizePolicy.Expanding, pya.QSizePolicy.Expanding)
        self.page_netlist.setSizePolicy(pya.QSizePolicy.Expanding, pya.QSizePolicy.Expanding)
    
    def _reload_netlist_tree(self, path: str) -> None:
        """Re-parse the netlist and refresh the content tree."""
        try:
            tree = self.page_netlist.netlist_content_tw
            if not path or not os.path.isfile(path):
                tree.clear()
                return
            try:
                # Preserve current settings when reloading
                current_settings = self.cell_import_settings_from_ui()
                self.populate_netlist_content_tree(
                    tree, path,
                    cell_import_settings=current_settings if current_settings else None,
                )
            except Exception as exc:
                tree.clear()
                err = pya.QTreeWidgetItem(tree)
                err.setText(0, f"Error: {exc}")
                traceback.print_exc()                
        except Exception as e:
            qmessagebox_critical(
                'Error',
                f"Failed to reload netlist content. ",
                f"Caught exception: <pre>{e}</pre>"
            )
            traceback.print_exc()
        
    def cell_import_settings_from_ui(self) -> List[CellImportSetting]:
        """Read cell/instance import settings from the netlist content tree."""
        tree = self.page_netlist.netlist_content_tw
        result = []
        root = tree.invisibleRootItem()
    
        for i in range(root.childCount()):
            cell_item = root.child(i)
            cell_name = cell_item.data(0, _CELL_NAME_ROLE)
            if not cell_name:
                continue
    
            # Read cell-level combo
            cb = tree.itemWidget(cell_item, 4)
            cell_mode = cb.itemData(cb.currentIndex) if cb else ImportMode.NEW_CELL.value
    
            # Read instance-level combos
            inst_settings = []
            for j in range(cell_item.childCount()):
                inst_item = cell_item.child(j)
                inst_cb = tree.itemWidget(inst_item, 4)
                inst_val = (inst_cb.itemData(inst_cb.currentIndex)
                            if inst_cb else ImportMode.TECH_CELL_MAPPING.value)
                inst_settings.append(InstanceImportSetting(
                    instance_name=inst_item.text(0),
                    device_name=inst_item.text(1),
                    import_mode=ImportMode(inst_val),
                    static_library=inst_item.data(0, _STATIC_LIBRARY_ROLE) or '',
                    static_cell=inst_item.data(0, _STATIC_CELL_ROLE) or '',
                ))
    
            result.append(CellImportSetting(
                cell_name=cell_name,
                import_mode=ImportMode(cell_mode),
                static_library=cell_item.data(0, _STATIC_LIBRARY_ROLE) or '',
                static_cell=cell_item.data(0, _STATIC_CELL_ROLE) or '',
                instance_settings=inst_settings,
            ))    
        return result
    
    def _parse_parameter_mapping(self, text: str) -> ParameterMapping:
        entries = {}
        for token in text.strip().split():
            if '=' in token:
                key, _, value = token.partition('=')
                entries[key.strip()] = value.strip()
        return ParameterMapping(entries=entries)
    
    def _format_parameter_mapping(self, pm: ParameterMapping) -> str:
        return ' '.join(f'{k}={v}' for k, v in pm.entries.items())
        
    def cell_map_from_ui(self, table_widget: pya.QTableWidget):
        entries = []
        for row in range(table_widget.rowCount):
            def cell_text(col):
                item = table_widget.item(row, col)
                return item.text if item is not None else ''
                
            # Read library from combo widget
            lib_cb = self._cell_map_lib_combos.get(row)
            lib_name = lib_cb.currentText.strip() if lib_cb else cell_text(2)
            
            # Read cell from combo widget
            cell_cb = self._cell_map_cell_combos.get(row)
            cell_name = cell_cb.currentText.strip() if cell_cb else cell_text(3)
                
            entries.append(CellMapEntry(
                netlist_device      = cell_text(0),
                layout_cell_type    = CellType(self._get_cell_type_value(row)),
                layout_cell_library = lib_name,
                layout_cell         = cell_name,
                parameter_mapping   = self._parse_parameter_mapping(cell_text(4))
            ))
        return CellMap(entries=entries)        
    
    def config_from_ui(self) -> NetlistImportConfig:
        return NetlistImportConfig(
            source_path=Path(self.source_path_w.path),
            file_format=NetlistFileFormat(self.page_netlist.file_format_cob.currentData()),
            hierarchy_mode=HierarchyMode(self.page_netlist.hierarchy_mode_cob.currentData()),
            cell_map=self.cell_map_from_ui(self.page_cell_map.cell_map_tw),
            cell_import_settings=self.cell_import_settings_from_ui(),
            origin_x=self.page_layout.origin_x_sb.value,
            origin_y=self.page_layout.origin_y_sb.value,
            limit_columns=self.page_layout.limit_columns_cb.checked,
            max_columns=self.page_layout.max_columns_sb.value,
            spacing=self.page_layout.spacing_sb.value
        )    
    
    def update_ui_from_config(self, config: NetlistImportConfig):
        if Debugging.DEBUG:
            debug("NetlistImportDialog.update_ui_from_config")
        
        self.source_path_w.path = str(config.source_path) if config.source_path else ''
        
        idx = self.page_netlist.file_format_cob.findData(config.file_format.value)
        if idx >= 0:
            self.page_netlist.file_format_cob.setCurrentIndex(idx)

        idx = self.page_netlist.hierarchy_mode_cob.findData(config.hierarchy_mode.value)
        if idx >= 0:
            self.page_netlist.hierarchy_mode_cob.setCurrentIndex(idx)
        
        self.page_layout.origin_x_sb.setValue(config.origin_x)
        self.page_layout.origin_y_sb.setValue(config.origin_y)
        
        self.page_layout.limit_columns_cb.setChecked(config.limit_columns)
        self.page_layout.max_columns_sb.setValue(config.max_columns)
        self.page_layout.spacing_sb.setValue(config.spacing)        

        self._cell_type_combos.clear()
        
        self.page_cell_map.cell_map_tw.blockSignals(True)
        self.page_cell_map.cell_map_tw.setRowCount(0)
        
        for row, e in enumerate(config.cell_map.entries):
            self.page_cell_map.cell_map_tw.insertRow(row)
            cells = [
                e.netlist_device,
                None,   # CellType handled separately
                None,   # Library handled separately
                None,   # Cell handled separately
                self._format_parameter_mapping(e.parameter_mapping)
            ]
            for col, value in enumerate(cells):
                if col == 1:  # cell type combo box
                    self._set_cell_type_widget(row, e.layout_cell_type.value)
                elif col == 2:
                    self._set_cell_map_library_widget(row, e.layout_cell_library)
                elif col == 3:
                    self._set_cell_map_cell_widget(row, e.layout_cell, e.layout_cell_library)
                else:
                    self.page_cell_map.cell_map_tw.setItem(row, col, self._make_data_item(value))
                
        self.page_cell_map.cell_map_tw.blockSignals(False)
            
        # Reload netlist tree with persisted import settings
        source = str(config.source_path) if config.source_path else ''
        if source and os.path.isfile(source):
            tree = self.page_netlist.netlist_content_tw
            try:
                self.populate_netlist_content_tree(
                    tree, source,
                    cell_import_settings=config.cell_import_settings,
                )
            except Exception:
                traceback.print_exc()
    
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
            table = self.page_cell_map.cell_map_tw
            row = table.rowCount
            table.blockSignals(True)
            table.insertRow(row)
            hints = ['SG13_LV_NMOS', None, 'SG13_dev', 'nmos', 'w=@w l=@l ng=@ng m=@m']
            for col, hint in enumerate(hints):
                if col == 1:
                    self._set_cell_type_widget(row, CellType.PCELL.value)
                else:
                    table.setItem(row, col, self._make_placeholder_item(hint))
            table.blockSignals(False)
            table.selectRow(row)
        except Exception as e:
            traceback.print_exc()
    
    def on_remove_cell_mapping(self):
        if Debugging.DEBUG:
            debug("NetlistImportDialog.on_remove_cell_mapping")

        try:
            selected_rows = sorted(set(index.row() for index in self.page_cell_map.cell_map_tw.selectedItems()), reverse=True)
            
            for row in selected_rows:
                self._cell_type_combos.pop(row, None)
                getattr(self, '_stashed_params', {}).pop(row, None)
                self.page_cell_map.cell_map_tw.removeRow(row)
            # Re-key remaining combos/params after row removal
            self._reindex_cell_type_combos()
            self._reindex_stashed_params()        
        except Exception as e:
            traceback.print_exc()
    
    def _reindex_cell_type_combos(self):
        """Rebuild the row→combo dict after rows are deleted."""
        new_type = {}
        new_lib = {}
        new_cell = {}
        for row in range(self.page_cell_map.cell_map_tw.rowCount):
            cb = self.page_cell_map.cell_map_tw.cellWidget(row, 1)
            if cb is not None:
                new_type[row] = cb
            cb = self.page_cell_map.cell_map_tw.cellWidget(row, 2)
            if cb is not None:
                new_lib[row] = cb
            cb = self.page_cell_map.cell_map_tw.cellWidget(row, 3)
            if cb is not None:
                new_cell[row] = cb
        self._cell_type_combos = new_type
        self._cell_map_lib_combos = new_lib
        self._cell_map_cell_combos = new_cell
        
    def _reindex_stashed_params(self):
        """Rebuild row keys in _stashed_params after row deletion."""
        if not hasattr(self, '_stashed_params'):
            return
        # The stashed entries with old row keys are now invalid;
        # we don't have a reliable way to remap, so just clear.
        # (Stash is only a convenience for undo within same session.)
        self._stashed_params.clear()
        
    def on_cell_map_selection_changed(self):
        if Debugging.DEBUG:
            debug("NetlistImportDialog.on_cell_map_selection_changed")
        selected = self.page_cell_map.cell_map_tw.selectedItems()
        self.page_cell_map.cell_map_remove_pb.setEnabled(bool(selected))


#--------------------------------------------------------------------------------

