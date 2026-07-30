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

from klayout_netlist_importer.netlist_import_config import *
from klayout_netlist_importer.page_base import PageBase

#--------------------------------------------------------------------------------

# Item-data roles
_PLACEHOLDER_ROLE    = int(pya.Qt.UserRole)
_CELL_NAME_ROLE      = int(pya.Qt.UserRole) + 1
_STATIC_LIBRARY_ROLE = int(pya.Qt.UserRole) + 2
_STATIC_CELL_ROLE    = int(pya.Qt.UserRole) + 3


class NetlistSourcePage(PageBase):
    def __init__(self,
                 library_helper: LibraryHelper,
                 on_goto_tech_cell_mapping: Callable[[str], None], 
                 on_add_tech_cell_mapping: Callable[[str], None],
                 get_active_cell_map_fn):
        super().__init__()
        
        self.library_helper = library_helper
        self._on_goto_tech_cell_mapping = on_goto_tech_cell_mapping
        self._on_add_tech_cell_mapping = on_add_tech_cell_mapping
        self._get_active_cell_map_fn = get_active_cell_map_fn
        
        # Keyed by id(item) to prevent pya GC
        self._import_setting_combos: dict  = {}
        self._import_settings_widgets: dict = {}
 
        # Public: the FileSelectorWidget created during setup
        self.source_path_w: FileSelectorWidget = None
        
        # Cached set of netlist cell names from the last successful parse,
        # used when refreshing individual col-5 widgets without a full tree rebuild.
        self._current_netlist_cell_names: Set[str] = set()
 
        self._setup()
        
    def widget(self) -> pya.QWidget:
        return self._widget
            
    def config_from_ui(self) -> NetlistSourceConfig:
        return NetlistSourceConfig(
            source_path=Path(self.source_path_w.path),
            file_format=NetlistFileFormat(self._widget.file_format_cob.currentData()),
            hierarchy_mode=HierarchyMode(self._widget.hierarchy_mode_cob.currentData()),
            cell_import_settings=self.cell_import_settings_from_ui(),
        )
        
    def update_ui_from_config(self, config: NetlistSourceConfig):
        self.source_path_w.path = str(config.source_path) if config.source_path else ''
        
        idx = self._widget.file_format_cob.findData(config.file_format.value)
        if idx >= 0:
            self._widget.file_format_cob.setCurrentIndex(idx)

        idx = self._widget.hierarchy_mode_cob.findData(config.hierarchy_mode.value)
        if idx >= 0:
            self._widget.hierarchy_mode_cob.setCurrentIndex(idx)

        # Reload netlist tree with persisted import settings
        source = str(config.source_path) if config.source_path else ''
        if source and os.path.isfile(source):
            tree = self._widget.netlist_content_tw
            try:
                self.populate_netlist_content_tree(
                    tree, source,
                    cell_import_settings=config.cell_import_settings,
                )
            except Exception:
                traceback.print_exc()
        
    def _setup(self):
        self._widget = load_ui('NetlistImportConfig_NetlistPage.ui', self)        
        p = self._widget
        
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
        
        self.source_path_w.on_path_changed += [self.on_netlist_path_changed]
        
        p.reload_netlist_pb.clicked.connect(lambda: self.on_netlist_path_changed(self.source_path_w))
        
        self._setup_netlist_content_tree()
                    
    def _replace_with_file_selector(self, 
                                    placeholder: pya.QWidget,
                                    **file_selector_kwargs) -> FileSelectorWidget:
        """Replace a placeholder widget with a FileSelectorWidget in-place."""
        
        widget = FileSelectorWidget(placeholder.parent, **file_selector_kwargs)
        widget.setSizePolicy(placeholder.sizePolicy)
        widget.setMinimumSize(placeholder.minimumSize)
        widget.setMaximumSize(placeholder.maximumSize)
        
        parent_layout = self._widget.source_file_layout
        parent_layout.removeWidget(placeholder)
        placeholder.hide()
        parent_layout.insertWidget(1, widget)
        widget.show()
        
        return widget
                   
    def _validate_static_cell_combo(self, lib_cb: pya.QComboBox, cell_cb: pya.QComboBox):
        """Set red background on library/cell combos if their value is invalid."""
        lib_name = lib_cb.currentText.strip()
        cell_name = cell_cb.currentText.strip()
    
        lib_valid = bool(lib_name) and lib_name in self.library_helper.get_library_names()
        cell_valid = False
        if lib_valid and cell_name:
            cell_valid = cell_name in self.library_helper.get_library_cell_names(lib_name)
    
        red = "QComboBox { background-color: #ffcccc; }"
        ok  = ""
        lib_cb.setStyleSheet(red if not lib_valid else ok)
        cell_cb.setStyleSheet(red if not cell_valid else ok)
                    
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
            ImportMode.NETLIST_CELL,
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
        tree = self._widget.netlist_content_tw

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
        self._widget.setSizePolicy(pya.QSizePolicy.Expanding, pya.QSizePolicy.Expanding)
    
    def _reload_netlist_tree(self, path: str) -> None:
        """Re-parse the netlist and refresh the content tree."""
        try:
            tree = self._widget.netlist_content_tw
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
        
        cv = pya.CellView.active()
        top_cell_name = cv.cell.name
        
        settings_map = {}
        if cell_import_settings:
            for cis in cell_import_settings:
                settings_map[cis.cell_name] = cis
        
        # ---- parse -------------------------------------------------------
        parser = NetlistParser()
        netlist = parser.parse(netlist_path, implicit_top_cell_name=top_cell_name)
     
        # ---- reset the tree ----------------------------------------------
        tree.clear()
        self._import_setting_combos.clear()
        self._import_settings_widgets.clear()
        tree.setHeaderHidden(False)     # columns defined in the .ui are kept
        
        netlist_cell_names = {c.name for c in netlist.all_cells}
        self._current_netlist_cell_names = netlist_cell_names
        
        for cell in netlist.all_cells:
            cis = settings_map.get(cell.name)
            self._add_cell_item(
                tree.invisibleRootItem(), cell,
                import_mode=cis.import_mode.value if cis else None,
                static_library=cis.static_library if cis else '',
                static_cell=cis.static_cell if cis else '',
                instance_settings=cis.instance_settings if cis else None,
                netlist_cell_names=netlist_cell_names,
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
        *, # everything after here is keyword-only
        netlist_cell_names: Set[str],
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
    
        cell_map = self._get_active_cell_map_fn()
    
        for inst in cell.instances:
            sis = inst_map.get(inst.name)
            self._add_instance_item(
                item, inst,
                import_mode=sis.import_mode.value if sis else None,
                static_library=sis.static_library if sis and hasattr(sis, 'static_library') else '',
                static_cell=sis.static_cell if sis and hasattr(sis, 'static_cell') else '',
                cell_map=cell_map,
                netlist_cell_names=netlist_cell_names,
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
        *, # everything after here is keyword-only
        netlist_cell_names: Set[str],
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
        if import_mode is None:  # no persisted setting
            if netlist_cell_names and inst.device_name in netlist_cell_names:
                effective_mode = ImportMode.NETLIST_CELL.value
            else:
                effective_mode = ImportMode.TECH_CELL_MAPPING.value
        else:
            effective_mode = import_mode
        cb = self._make_instance_import_setting_combo(effective_mode)
        self._import_setting_combos[id(item)] = cb
        tree.setItemWidget(item, 4, cb)
    
        # Store static cell lib/cell in item data roles so they survive tree rebuilds
        item.setData(0, _STATIC_LIBRARY_ROLE, static_library or '')
        item.setData(0, _STATIC_CELL_ROLE,    static_cell    or '')
 
        # Col 5 – Import Settings widget (mode-dependent)
        self._refresh_import_settings_widget(tree, item, inst.device_name or '', effective_mode, cell_map,
                                             netlist_cell_names=netlist_cell_names)
 
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
            try:
                live_cell_map = self._get_active_cell_map_fn()
            except Exception:
                traceback.print_exc()
                live_cell_map = cell_map  # fall back to snapshot
            self._refresh_import_settings_widget(tree, item, device_name, mode, live_cell_map,
                                                 netlist_cell_names=self._current_netlist_cell_names)
        
        EventLoop.defer(_refresh)
    
    def _refresh_import_settings_widget(self, tree, item, device_name, mode, cell_map,
                                        netlist_cell_names: Set[str]):
        """Build and install the correct Import Settings widget in col 5."""
        
        old_widget = self._import_settings_widgets.get(id(item))
        
        w = self._make_import_settings_widget(item, device_name, mode, cell_map,
                                              netlist_cell_names=netlist_cell_names)
        if w is None:
            # Install an empty transparent widget so the column stays blank
            w = pya.QWidget()
        # Always store a reference (even None → use empty widget) to prevent GC
        self._import_settings_widgets[id(item)] = w
        tree.setItemWidget(item, 5, w)
 
    def _make_import_settings_widget(self, item, device_name, mode, cell_map,
                                     netlist_cell_names: Set[str]):
        """Return the appropriate widget for col 5 depending on *mode*.
 
        TECH_CELL_MAPPING
            • No match in cell_map → red warning label + [Ignore] + [Add →] buttons
            • Match found          → lib/cell label + [→] navigate button
        NETLIST_CELL
            • device_name not in netlist_cell_names → red warning label + [Ignore] button
            • device_name found                     → [→] navigate button
        EXTERNAL_STATIC_CELL
            • Library: [QLineEdit]  Cell: [QLineEdit]
        IGNORE / other
            → None (empty)
        """
        if mode == ImportMode.TECH_CELL_MAPPING.value:
            return self._make_tech_mapping_widget(item, device_name, cell_map, instance=True)
        elif mode == ImportMode.EXTERNAL_STATIC_CELL.value:
            return self._make_static_cell_widget(item, instance=True)
        elif mode == ImportMode.NETLIST_CELL.value:
            return self._make_netlist_cell_widget(item, device_name, netlist_cell_names)
        else:
            return None
 
    @property
    def cell_button_height(self) -> int:
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
            ignore_btn.setFixedHeight(self.cell_button_height)
            ignore_btn.clicked.connect(
                lambda checked=False, it=item:
                    self._set_instance_mode(it, ImportMode.IGNORE.value)
            )
 
            add_btn = pya.QPushButton("Add ▶")
            add_btn.setToolTip(
                f"Add a new Tech Cell Mapping entry for '{device_name}' "
                f"and navigate to the Tech Cell Mapping page"
            )
            add_btn.setFixedHeight(self.cell_button_height)
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
            goto_btn.setFixedSize(28, self.cell_button_height)
            goto_btn.clicked.connect(
                lambda checked=False, dev=device_name:
                    self._on_goto_tech_cell_mapping(dev)
            )
 
            layout.addWidget(info_lbl)
            layout.addWidget(goto_btn)
            layout.addStretch(1)
 
        return container
 
    def cell_import_settings_from_ui(self) -> List[CellImportSetting]:
        """Read cell/instance import settings from the netlist content tree."""
        tree = self._widget.netlist_content_tw
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
    
    def _make_static_cell_widget(self, item, instance=False):
        """Widget for EXTERNAL_STATIC_CELL mode in col 5: Library + Cell combos."""
        saved_lib  = item.data(0, _STATIC_LIBRARY_ROLE) or ''
        saved_cell = item.data(0, _STATIC_CELL_ROLE)    or ''
 
        container  = pya.QWidget()
        grey_style = "color: #646464;" if instance else ""
        layout     = pya.QHBoxLayout(container)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(4)
 
        lbl_lib = pya.QLabel("Library:")
        if instance:
            lbl_lib.setStyleSheet(grey_style)
        lib_cb = pya.QComboBox()
        lib_cb.setEditable(True)
        lib_cb.setMinimumWidth(100)
 
        lbl_cell = pya.QLabel("Cell:")
        if instance:
            lbl_cell.setStyleSheet(grey_style)
        cell_cb = pya.QComboBox()
        cell_cb.setEditable(True)
        cell_cb.setMinimumWidth(100)
 
        def _populate_cell_combo(lib_name: str):
            prev_cell = cell_cb.currentText
            cell_cb.blockSignals(True)
            cell_cb.clear()
            cell_cb.addItem("")
            for cname in self.library_helper.get_library_cell_names(lib_name):
                cell_cb.addItem(cname)
            idx = cell_cb.findText(prev_cell)
            if idx >= 0:
                cell_cb.setCurrentIndex(idx)
            else:
                cell_cb.setEditText(prev_cell)
            cell_cb.blockSignals(False)
 
        lib_cb.blockSignals(True)
        lib_cb.addItem("")
        for name in self.library_helper.get_library_names():
            lib_cb.addItem(name)
        idx = lib_cb.findText(saved_lib)
        if idx >= 0:
            lib_cb.setCurrentIndex(idx)
        else:
            lib_cb.setEditText(saved_lib)
        
        lib_cb.blockSignals(False)
 
        cell_cb.blockSignals(True)
        _populate_cell_combo(saved_lib)
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
 
        self._validate_static_cell_combo(lib_cb, cell_cb)
 
        container.setMinimumWidth(200)
 
        # Keep Python references to child widgets alive to prevent pya GC crash
        key = id(item)
        self._import_settings_widgets[('children', key)] = [
            lbl_lib, lib_cb, lbl_cell, cell_cb, layout
        ]
 
        return container
 
    def _make_netlist_cell_widget(self, 
                                  item, 
                                  device_name: str,
                                  netlist_cell_names: Set[str]):
        """Widget for NETLIST_CELL mode in col 5.
        
        When *device_name* is not found in *netlist_cell_names* a red warning
        with an [Ignore] button is shown, mirroring the TECH_CELL_MAPPING
        no-match case.  When the cell is found a plain [▶] navigate button is
        shown instead.
        """
        cell_found = (
            bool(device_name)
            and netlist_cell_names is not None
            and device_name in netlist_cell_names
        )
    
        container = pya.QWidget()
        layout    = pya.QHBoxLayout(container)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(4)
 
        if not cell_found:
            # ── No matching netlist cell: red warning ───────────────────────
            warn_lbl = pya.QLabel("⚠ Netlist cell not found")
            warn_lbl.setStyleSheet("color: #c0392b; font-weight: bold;")
            warn_lbl.setToolTip(
                f"No subckt named '{device_name}' was found in the netlist.\n"
                f"This instance will be skipped during import."
            )

            ignore_btn = pya.QPushButton("Ignore")
            ignore_btn.setToolTip("Switch this instance to Ignore mode")
            ignore_btn.setFixedHeight(self.cell_button_height)
            ignore_btn.clicked.connect(
                lambda checked=False, it=item:
                    self._set_instance_mode(it, ImportMode.IGNORE.value)
            )

            layout.addWidget(warn_lbl)
            layout.addWidget(ignore_btn)
            layout.addStretch(1)
        else:
            goto_btn = pya.QPushButton("▶")
            goto_btn.setToolTip("Go to and select this netlist cell")
            goto_btn.setFixedSize(28, self.cell_button_height)
            goto_btn.clicked.connect(
                lambda checked=False, dev=device_name:
                    self._on_goto_netlist_cell(dev)
            )
 
            layout.addWidget(goto_btn)
            layout.addStretch(1)
 
        return container
 
    def _on_goto_netlist_cell(self, device_name: str):
        """Scroll the netlist content tree to the cell row for *device_name*."""
        tree = self._widget.netlist_content_tw
        root = tree.invisibleRootItem()
        for i in range(root.childCount()):
            cell_item = root.child(i)
            if cell_item.data(0, _CELL_NAME_ROLE) == device_name:
                tree.setCurrentItem(cell_item)
                tree.scrollToItem(cell_item)
                break

    def _set_instance_mode(self, item: pya.QTreeWidgetItem, mode_value: str):
        """Programmatically change the Import Mode combo for an instance row."""
        tree = self._widget.netlist_content_tw
        cb = tree.itemWidget(item, 4)
        if cb is None:
            return
        for i in range(cb.count):
            if cb.itemData(i) == mode_value:
                cb.setCurrentIndex(i)
                break

    def refresh_tech_mapping_widgets(self):
        """Re-render col-5 for all TECH_CELL_MAPPING instance rows.
        
        Called when the tech cell map changes so stale match/no-match
        indicators are updated without rebuilding the whole tree.
        """
        try:
            cell_map = self._get_active_cell_map_fn()
        except Exception:
            traceback.print_exc()
            return

        tree = self._widget.netlist_content_tw
        root = tree.invisibleRootItem()

        for i in range(root.childCount()):
            cell_item = root.child(i)
            for j in range(cell_item.childCount()):
                inst_item = cell_item.child(j)
                cb = tree.itemWidget(inst_item, 4)
                if cb is None:
                    continue
                mode = cb.itemData(cb.currentIndex)
                if mode != ImportMode.TECH_CELL_MAPPING.value:
                    continue
                device_name = inst_item.text(1)  # col 1 holds the device name
                self._refresh_import_settings_widget(
                    tree, inst_item, device_name, mode, cell_map,
                    netlist_cell_names=self._current_netlist_cell_names,
                )
