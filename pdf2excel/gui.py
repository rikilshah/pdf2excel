from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QSettings, Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QInputDialog, QLineEdit as QtLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton, QSplitter, QStatusBar,
    QTableView, QTableWidget, QTableWidgetItem, QTextEdit, QToolBar, QVBoxLayout, QWidget,
)

from .corrections import CorrectionError, apply_instruction
from .extraction import InvalidPasswordError, PasswordRequiredError, extract_pdf
from .mapping import MappingSpec, apply_mapping, suggest_mapping
from .models import ExtractionResult
from .tabular import export_data, read_excel_headers
from .validation import validate


class DataModel(QAbstractTableModel):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.columns: list[str] = []
        self.rows: list[dict[str, str]] = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        value = self.rows[index.row()].get(self.columns[index.column()], "")
        if role in (Qt.DisplayRole, Qt.EditRole):
            return value
        if role == Qt.BackgroundRole and not str(value).strip():
            return QColor("#fff8e6")
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid():
            return False
        self.rows[index.row()][self.columns[index.column()]] = str(value)
        self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
        self.changed.emit()
        return True

    def flags(self, index):
        return super().flags(index) | Qt.ItemIsEditable if index.isValid() else Qt.NoItemFlags

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return self.columns[section] if orientation == Qt.Horizontal else str(section + 1)
        return None

    def replace(self, columns: list[str], rows: list[dict[str, str]]):
        self.beginResetModel()
        self.columns, self.rows = columns[:], [{c: str(r.get(c, "") or "") for c in columns} for r in rows]
        self.endResetModel()
        self.changed.emit()

    def remove_rows(self, indexes: list[int]):
        if not indexes:
            return
        self.beginResetModel()
        for row in sorted(set(indexes), reverse=True):
            if 0 <= row < len(self.rows):
                del self.rows[row]
        self.endResetModel()
        self.changed.emit()


class ExtractWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    password_required = Signal(str)

    def __init__(self, path: Path, mode: str, password: str | None):
        super().__init__()
        self.path, self.mode, self.password = path, mode, password

    def run(self):
        try:
            self.finished.emit(extract_pdf(self.path, allow_ocr=True, mode=self.mode, password=self.password))
        except PasswordRequiredError:
            self.password_required.emit("required")
        except InvalidPasswordError:
            self.password_required.emit("invalid")
        except Exception as exc:
            self.failed.emit(str(exc))


class MappingDialog(QDialog):
    def __init__(self, source_columns: list[str], target_columns: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Map PDF data to Excel columns")
        self.resize(760, 560)
        self.source_columns, self.target_columns = source_columns, target_columns
        suggestions = suggest_mapping(target_columns, source_columns)
        root = QVBoxLayout(self)
        note = QLabel("Select one or more detected columns for each Excel column. Multiple selections are joined with a space. Unselected targets remain blank.")
        note.setWordWrap(True); note.setObjectName("muted")
        root.addWidget(note)
        self.table = QTableWidget(len(target_columns), 2)
        self.table.setHorizontalHeaderLabels(["Excel target column", "Detected PDF column(s)"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.lists: list[QListWidget] = []
        for row, target in enumerate(target_columns):
            item = QTableWidgetItem(target); item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, item)
            picker = QListWidget(); picker.setSelectionMode(QListWidget.MultiSelection); picker.setMaximumHeight(82)
            for source in source_columns:
                option = QListWidgetItem(source); picker.addItem(option)
                if source in suggestions.get(target, []):
                    option.setSelected(True)
            self.table.setCellWidget(row, 1, picker); self.lists.append(picker)
            self.table.setRowHeight(row, 88)
        root.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.accepted.connect(self._accept_checked); buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _accept_checked(self):
        uses: dict[str, list[str]] = {}
        for target, picker in zip(self.target_columns, self.lists):
            for item in picker.selectedItems():
                uses.setdefault(item.text(), []).append(target)
        duplicates = {source: targets for source, targets in uses.items() if len(targets) > 1}
        if duplicates:
            detail = "\n".join(f"{s} -> {', '.join(t)}" for s, t in duplicates.items())
            answer = QMessageBox.question(self, "Duplicate mappings", f"Some detected columns are used more than once:\n\n{detail}\n\nContinue?")
            if answer != QMessageBox.Yes:
                return
        self.accept()

    def mapping(self) -> MappingSpec:
        return MappingSpec({target: [i.text() for i in picker.selectedItems()]
                            for target, picker in zip(self.target_columns, self.lists)})


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF2Excel Mapper")
        self.resize(1280, 780)
        self.settings = QSettings("PDF2Excel", "Mapper")
        self.pdf_path: Path | None = None
        self.extraction: ExtractionResult | None = None
        self._pdf_password: str | None = None
        self.model = DataModel()
        self.model.changed.connect(self.refresh_summary)
        self._thread: QThread | None = None
        self._retry_after_password = False
        self._build_ui()
        self._apply_style()

    def _build_ui(self):
        toolbar = QToolBar("Main"); toolbar.setMovable(False); toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)
        open_action = QAction("Open PDF", self); open_action.setShortcut(QKeySequence.Open); open_action.triggered.connect(self.open_pdf)
        export_action = QAction("Export detected data", self); export_action.setShortcut(QKeySequence.Save); export_action.triggered.connect(self.export_direct)
        map_action = QAction("Map to Excel template", self); map_action.triggered.connect(self.map_to_excel)
        toolbar.addAction(open_action); toolbar.addSeparator(); toolbar.addAction(export_action); toolbar.addAction(map_action)

        central = QWidget(); outer = QVBoxLayout(central); outer.setContentsMargins(18, 16, 18, 14); outer.setSpacing(12)
        title_row = QHBoxLayout()
        title_box = QVBoxLayout(); title = QLabel("PDF table workspace"); title.setObjectName("title")
        self.subtitle = QLabel("Open a PDF to extract, review, correct, and export its tabular data."); self.subtitle.setObjectName("muted")
        title_box.addWidget(title); title_box.addWidget(self.subtitle); title_row.addLayout(title_box, 1)
        self.mode = QComboBox(); self.mode.addItems(["Auto detect", "Ruled tables (lines)", "Borderless tables (text)", "Force OCR"])
        self.reextract = QPushButton("Re-extract"); self.reextract.clicked.connect(self.start_extraction); self.reextract.setEnabled(False)
        title_row.addWidget(QLabel("Method")); title_row.addWidget(self.mode); title_row.addWidget(self.reextract)
        outer.addLayout(title_row)

        splitter = QSplitter(Qt.Horizontal)
        table_panel = QFrame(); table_panel.setObjectName("panel"); table_layout = QVBoxLayout(table_panel)
        table_head = QHBoxLayout(); self.summary = QLabel("No data loaded"); self.summary.setObjectName("section")
        delete_btn = QPushButton("Delete selected rows"); delete_btn.clicked.connect(self.delete_selected)
        table_head.addWidget(self.summary); table_head.addStretch(); table_head.addWidget(delete_btn)
        self.table = QTableView(); self.table.setModel(self.model); self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectItems); self.table.setSelectionMode(QTableView.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive); self.table.horizontalHeader().setDefaultSectionSize(150)
        self.table.verticalHeader().setDefaultSectionSize(28)
        table_layout.addLayout(table_head); table_layout.addWidget(self.table)

        side = QFrame(); side.setObjectName("panel"); side.setMinimumWidth(300); side.setMaximumWidth(430)
        side_layout = QVBoxLayout(side)
        correction_title = QLabel("Correction assistant"); correction_title.setObjectName("section")
        help_text = QLabel("Edit any cell directly, delete selected rows, or enter a correction command.\n\nExamples:\n• rename Qty to Quantity\n• remove row 3\n• use row 2 as headers\n• split Name by , into First,Last\n• merge First + Last into Name")
        help_text.setWordWrap(True); help_text.setObjectName("muted")
        self.instruction = QLineEdit(); self.instruction.setPlaceholderText("Describe one correction…"); self.instruction.returnPressed.connect(self.apply_correction)
        apply_btn = QPushButton("Apply correction"); apply_btn.setObjectName("primary"); apply_btn.clicked.connect(self.apply_correction)
        warning_title = QLabel("Extraction report"); warning_title.setObjectName("section")
        self.report = QTextEdit(); self.report.setReadOnly(True); self.report.setPlaceholderText("Warnings and validation results appear here.")
        side_layout.addWidget(correction_title); side_layout.addWidget(help_text); side_layout.addWidget(self.instruction); side_layout.addWidget(apply_btn)
        side_layout.addSpacing(12); side_layout.addWidget(warning_title); side_layout.addWidget(self.report, 1)
        splitter.addWidget(table_panel); splitter.addWidget(side); splitter.setStretchFactor(0, 1); splitter.setSizes([900, 340])
        outer.addWidget(splitter, 1)

        self.progress = QProgressBar(); self.progress.setRange(0, 0); self.progress.hide(); outer.addWidget(self.progress)
        self.setCentralWidget(central); self.setStatusBar(QStatusBar())

    def _apply_style(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #f5f7fb; color: #172033; font: 10pt 'Segoe UI'; }
            QToolBar { background: #ffffff; border: 0; border-bottom: 1px solid #dfe4ec; padding: 8px 14px; spacing: 8px; }
            QToolButton { padding: 7px 10px; border-radius: 6px; } QToolButton:hover { background: #edf2ff; }
            QFrame#panel { background: #ffffff; border: 1px solid #dfe4ec; border-radius: 10px; }
            QLabel#title { font-size: 20pt; font-weight: 650; color: #111827; }
            QLabel#section { font-size: 12pt; font-weight: 650; color: #111827; }
            QLabel#muted { color: #667085; }
            QPushButton, QComboBox, QLineEdit { min-height: 32px; border: 1px solid #cfd6e4; border-radius: 7px; padding: 0 10px; background: #ffffff; }
            QPushButton:hover { border-color: #7395e5; background: #f5f8ff; }
            QPushButton#primary { color: white; background: #315fcb; border-color: #315fcb; font-weight: 600; }
            QPushButton#primary:hover { background: #264fac; }
            QTableView, QTableWidget, QListWidget, QTextEdit { background: white; border: 1px solid #dfe4ec; border-radius: 6px; gridline-color: #e8ecf2; alternate-background-color: #f8faff; }
            QHeaderView::section { background: #edf2f8; color: #344054; padding: 7px; border: 0; border-right: 1px solid #dfe4ec; border-bottom: 1px solid #dfe4ec; font-weight: 600; }
            QStatusBar { background: #ffffff; border-top: 1px solid #dfe4ec; }
            QProgressBar { border: 0; background: #e9edf5; height: 5px; } QProgressBar::chunk { background: #315fcb; }
        """)

    def open_pdf(self):
        start = self.settings.value("lastFolder", str(Path.home()))
        name, _ = QFileDialog.getOpenFileName(self, "Open PDF", start, "PDF documents (*.pdf)")
        if not name:
            return
        self.pdf_path = Path(name); self._pdf_password = None; self.settings.setValue("lastFolder", str(self.pdf_path.parent)); self.start_extraction()

    def start_extraction(self):
        if not self.pdf_path or self._thread:
            return
        modes = ["auto", "lines", "text", "ocr"]; mode = modes[self.mode.currentIndex()]
        self.progress.show(); self.reextract.setEnabled(False); self.statusBar().showMessage(f"Extracting with {self.mode.currentText()}…")
        thread = QThread(self); worker = ExtractWorker(self.pdf_path, mode, self._pdf_password); worker.moveToThread(thread)
        thread.started.connect(worker.run); worker.finished.connect(self.extraction_done); worker.failed.connect(self.extraction_failed)
        worker.password_required.connect(self.request_pdf_password)
        worker.finished.connect(thread.quit); worker.failed.connect(thread.quit); worker.password_required.connect(thread.quit); thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater); thread.finished.connect(self._thread_finished)
        self._thread = thread; self._worker = worker; thread.start()

    def _thread_finished(self):
        self._thread = None; self.progress.hide(); self.reextract.setEnabled(bool(self.pdf_path))
        if self._retry_after_password:
            self._retry_after_password = False
            self.start_extraction()

    def extraction_done(self, result: ExtractionResult):
        self.extraction = result; self.model.replace(result.columns, result.rows)
        self.subtitle.setText(str(self.pdf_path)); self.statusBar().showMessage("Extraction complete. Review highlighted blanks and warnings.", 6000)
        self.refresh_report()

    def extraction_failed(self, message: str):
        QMessageBox.critical(self, "Extraction failed", message); self.statusBar().showMessage("Extraction failed", 5000)

    def request_pdf_password(self, reason: str):
        title = "Incorrect password" if reason == "invalid" else "Password required"
        prompt = "The password was incorrect. Try again:" if reason == "invalid" else "This PDF is protected. Enter its password:"
        password, accepted = QInputDialog.getText(self, title, prompt, QtLineEdit.Password)
        if accepted:
            self._pdf_password = password
            self._retry_after_password = True
        else:
            self.statusBar().showMessage("Password entry cancelled; no data was extracted.", 5000)

    def refresh_summary(self):
        self.summary.setText(f"{len(self.model.rows):,} rows  •  {len(self.model.columns)} columns")

    def refresh_report(self):
        if not self.extraction:
            return
        current = ExtractionResult(self.model.columns, self.model.rows, self.extraction.tables, self.extraction.warnings, self.extraction.used_ocr)
        issues = validate(current, self.model.rows)
        self.report.setPlainText("No validation warnings." if not issues else "\n\n".join(f"{i.level}: {i.message}" for i in issues))

    def apply_correction(self):
        if not self.model.columns:
            return
        try:
            columns, rows, message = apply_instruction(self.model.columns, self.model.rows, self.instruction.text())
            self.model.replace(columns, rows); self.instruction.clear(); self.refresh_report(); self.statusBar().showMessage(message, 6000)
        except CorrectionError as exc:
            QMessageBox.warning(self, "Cannot apply correction", str(exc))

    def delete_selected(self):
        rows = [i.row() for i in self.table.selectionModel().selectedIndexes()]
        if rows and QMessageBox.question(self, "Delete rows", f"Delete {len(set(rows))} selected row(s)?") == QMessageBox.Yes:
            self.model.remove_rows(rows); self.refresh_report()

    def export_direct(self):
        if not self.model.rows:
            QMessageBox.information(self, "Nothing to export", "Open and extract a PDF first."); return
        default = str((self.pdf_path or Path("converted")).with_name(f"{(self.pdf_path or Path('converted')).stem}_converted.xlsx"))
        name, selected = QFileDialog.getSaveFileName(self, "Export detected data", default, "Excel workbook (*.xlsx);;CSV UTF-8 (*.csv);;Tab-delimited text (*.txt)")
        if not name:
            return
        path = Path(name)
        if not path.suffix:
            path = path.with_suffix(".csv" if "CSV" in selected else ".txt" if "text" in selected else ".xlsx")
        self._write_export(self.model.rows, self.model.columns, path)

    def map_to_excel(self):
        if not self.model.rows:
            QMessageBox.information(self, "Nothing to map", "Open and extract a PDF first."); return
        name, _ = QFileDialog.getOpenFileName(self, "Select Excel template", self.settings.value("lastFolder", ""), "Excel workbooks (*.xlsx)")
        if not name:
            return
        try:
            headers, sheet = read_excel_headers(Path(name))
        except Exception as exc:
            QMessageBox.critical(self, "Cannot read template", str(exc)); return
        dialog = MappingDialog(self.model.columns, headers, self)
        if dialog.exec() != QDialog.Accepted:
            return
        mapped = apply_mapping(self.model.rows, dialog.mapping())
        default = str(Path(name).with_name(f"{Path(name).stem}_mapped.xlsx"))
        output, selected = QFileDialog.getSaveFileName(self, f"Export mapped data ({sheet})", default, "Excel workbook (*.xlsx);;CSV UTF-8 (*.csv);;Tab-delimited text (*.txt)")
        if output:
            path = Path(output)
            if not path.suffix:
                path = path.with_suffix(".csv" if "CSV" in selected else ".txt" if "text" in selected else ".xlsx")
            self._write_export(mapped, headers, path)

    def _write_export(self, rows, columns, path: Path):
        try:
            count = export_data(rows, columns, path)
            self.statusBar().showMessage(f"Exported {count:,} rows to {path}", 8000)
            QMessageBox.information(self, "Export complete", f"Exported {count:,} rows.\n\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PDF2Excel Mapper"); app.setOrganizationName("PDF2Excel")
    app.setStyle("Fusion")
    window = MainWindow(); window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
