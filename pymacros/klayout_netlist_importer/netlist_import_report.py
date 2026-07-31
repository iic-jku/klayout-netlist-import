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
from dataclasses import dataclass, field
import traceback
from typing import *

import pya

from klayout_plugin_utils.debugging import debug, Debugging



@dataclass
class NetlistImportReport:
    """Summary of one import_netlist_into_layout() run.

    Cells and instances are tracked separately because a cell can be
    successfully created even if some of its instances fail (and vice
    versa a cell's creation can fail, which then cascades into all of its
    instances being skipped).
    """
    succeeded_cells: List[str] = field(default_factory=list)
    # cell name -> list of reasons the cell itself could not be created/resolved
    failed_cells: Dict[str, List[str]] = field(default_factory=dict)

    succeeded_instances: List[str] = field(default_factory=list)  # "cell_name.instance_name"
    # "cell_name.instance_name" -> reason
    failed_instances: Dict[str, str] = field(default_factory=dict)

    def add_cell_success(self, cell_name: str):
        self.succeeded_cells.append(cell_name)

    def add_cell_failure(self, cell_name: str, reason: str):
        self.failed_cells.setdefault(cell_name, []).append(reason)

    def add_instance_success(self, cell_name: str, instance_name: str):
        self.succeeded_instances.append(f"{cell_name}.{instance_name}")

    def add_instance_failure(self, cell_name: str, instance_name: str, reason: str):
        self.failed_instances[f"{cell_name}.{instance_name}"] = reason

    @property
    def has_failures(self) -> bool:
        return bool(self.failed_cells) or bool(self.failed_instances)

    @property
    def all_errors(self) -> List[str]:
        """Flat list of error strings, preserved for backward compatibility
        with the old flat `errors: List[str]` collection."""
        errs: List[str] = []
        for cell_name, reasons in self.failed_cells.items():
            for reason in reasons:
                errs.append(f"[cell '{cell_name}'] {reason}")
        for key, reason in self.failed_instances.items():
            errs.append(f"[instance '{key}'] {reason}")
        return errs

    def summary_text(self) -> str:
        """Human-readable report, suitable for a message box or log file."""
        lines: List[str] = []
        lines.append("Netlist Import Report")
        lines.append("======================")
        lines.append("")
        lines.append(f"Cells succeeded: {len(self.succeeded_cells)}")
        lines.append(f"Cells failed:    {len(self.failed_cells)}")
        lines.append(f"Instances succeeded: {len(self.succeeded_instances)}")
        lines.append(f"Instances failed:    {len(self.failed_instances)}")
        lines.append("")

        if self.succeeded_cells:
            lines.append("Succeeded cells:")
            for name in self.succeeded_cells:
                lines.append(f"  ✔ {name}")
            lines.append("")

        if self.failed_cells:
            lines.append("Failed cells:")
            for name, reasons in self.failed_cells.items():
                lines.append(f"  ✘ {name}")
                for reason in reasons:
                    lines.append(f"      - {reason}")
            lines.append("")

        if self.failed_instances:
            lines.append("Failed instances:")
            for key, reason in self.failed_instances.items():
                lines.append(f"  ✘ {key}: {reason}")
            lines.append("")

        return "\n".join(lines)

