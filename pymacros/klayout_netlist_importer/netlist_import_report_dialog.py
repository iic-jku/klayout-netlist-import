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

import pya

from klayout_netlist_importer.netlist_import_report import NetlistImportReport


class NetlistImportReportDialog(pya.QDialog):
    """Resizable dialog showing the full ImportReport summary.

    Unlike a QMessageBox with setDetailedText(), this dialog:
      - is resizable (user can drag to see long reports comfortably)
      - always shows the full report text (no Show Details/Hide toggle)
      - only has a single OK button, bottom-right
    """

    def __init__(self, report: NetlistImportReport, parent=None):
        super().__init__(parent)

        if report.has_failures:
            self.setWindowTitle("Import completed with errors")
        else:
            self.setWindowTitle("Import completed successfully")

        self.resize(700, 500)
        self.setSizeGripEnabled(True)

        layout = pya.QVBoxLayout(self)

        summary = pya.QLabel(
            f"Cells: {len(report.succeeded_cells)} succeeded, "
            f"{len(report.failed_cells)} failed\n"
            f"Instances: {len(report.succeeded_instances)} succeeded, "
            f"{len(report.failed_instances)} failed"
        )
        layout.addWidget(summary)

        text_edit = pya.QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setLineWrapMode(pya.QTextEdit.NoWrap)
        text_edit.setText(report.summary_text())
        # Monospace makes the indented cell/instance list line up
        font = pya.QFont("Courier New")
        font.setStyleHint(pya.QFont.Monospace)
        text_edit.setFont(font)
        layout.addWidget(text_edit, 1)  # stretch factor 1: grows/shrinks with the dialog

        button_row = pya.QHBoxLayout()
        button_row.addStretch(1)  # pushes OK to the right

        ok_button = pya.QPushButton("OK")
        ok_button.setAutoDefault(True)
        ok_button.setDefault(True)
        ok_button.clicked.connect(self._on_ok_clicked)
        button_row.addWidget(ok_button)

        layout.addLayout(button_row)

        self.setLayout(layout)

    def _on_ok_clicked(self, checked: bool = False):
        self.accept()
