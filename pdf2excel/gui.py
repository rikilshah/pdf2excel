from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QPointF, QRectF, QSettings, Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QInputDialog, QLineEdit as QtLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton, QSplitter, QStatusBar,
    QTableView, QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QToolBar, QVBoxLayout, QWidget,
)

from .corrections import CorrectionError, apply_instruction
from .extraction import InvalidPasswordError, PasswordRequiredError, extract_pdf, extract_selected_ocr
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

    def rename_column(self, section: int, new_name: str) -> None:
        if section < 0 or section >= len(self.columns):
            return
        new_name = new_name.strip()
        old_name = self.columns[section]
        if not new_name or (new_name != old_name and new_name in self.columns):
            raise ValueError("Column names must be nonblank and unique.")
        if new_name == old_name:
            return
        self.columns[section] = new_name
        for row in self.rows:
            row[new_name] = row.pop(old_name, "")
        self.headerDataChanged.emit(Qt.Horizontal, section, section)
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


class GuidedOcrWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    password_required = Signal(str)

    def __init__(self, path: Path, box, boundaries, page_index, all_pages, password):
        super().__init__()
        self.args = path, box, boundaries, page_index, all_pages, password

    def run(self):
        try:
            self.finished.emit(extract_selected_ocr(*self.args))
        except PasswordRequiredError:
            self.password_required.emit("required")
        except InvalidPasswordError:
            self.password_required.emit("invalid")
        except Exception as exc:
            self.failed.emit(str(exc))


class SelectionCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(640, 460)
        self.setMouseTracking(True)
        self.image = QImage()
        self.selection: QRectF | None = None
        self.dividers: list[float] = []
        self.mode = "area"
        self._drag_start: QPointF | None = None

    def set_image(self, image: QImage):
        self.image = image
        self.selection = None; self.dividers = []
        self.update()

    def _target(self) -> QRectF:
        if self.image.isNull():
            return QRectF()
        scale = min(self.width() / self.image.width(), self.height() / self.image.height())
        width, height = self.image.width() * scale, self.image.height() * scale
        return QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)

    def _to_image(self, point: QPointF) -> QPointF | None:
        target = self._target()
        if not target.contains(point):
            return None
        return QPointF((point.x() - target.left()) * self.image.width() / target.width(),
                       (point.y() - target.top()) * self.image.height() / target.height())

    def paintEvent(self, event):
        painter = QPainter(self); painter.fillRect(self.rect(), QColor("#283142"))
        target = self._target()
        if self.image.isNull():
            painter.setPen(QColor("#cbd5e1")); painter.drawText(self.rect(), Qt.AlignCenter, "Open a PDF to preview pages")
            return
        painter.drawImage(target, self.image)
        if self.selection:
            sx = target.left() + self.selection.left() * target.width() / self.image.width()
            sy = target.top() + self.selection.top() * target.height() / self.image.height()
            sw = self.selection.width() * target.width() / self.image.width()
            sh = self.selection.height() * target.height() / self.image.height()
            shown = QRectF(sx, sy, sw, sh)
            painter.fillRect(shown, QColor(49, 95, 203, 35)); painter.setPen(QPen(QColor("#315fcb"), 2)); painter.drawRect(shown)
            painter.setPen(QPen(QColor("#ef4444"), 2))
            for x in self.dividers:
                px = target.left() + x * target.width() / self.image.width()
                painter.drawLine(round(px), round(shown.top()), round(px), round(shown.bottom()))

    def mousePressEvent(self, event):
        point = self._to_image(event.position())
        if point is None:
            return
        if self.mode == "divider" and self.selection and self.selection.left() < point.x() < self.selection.right():
            self.dividers.append(point.x()); self.dividers = sorted(set(self.dividers)); self.update(); return
        self.mode = "area"; self._drag_start = point; self.selection = QRectF(point, point); self.dividers = []; self.update()

    def mouseMoveEvent(self, event):
        if self._drag_start is None:
            return
        point = self._to_image(event.position())
        if point:
            self.selection = QRectF(self._drag_start, point).normalized(); self.update()

    def mouseReleaseEvent(self, event):
        self._drag_start = None

    def geometry(self):
        if not self.selection or self.selection.width() < 10 or self.selection.height() < 10:
            raise ValueError("Drag a table rectangle first.")
        boundaries = [self.selection.left(), *self.dividers, self.selection.right()]
        if len(boundaries) < 3:
            raise ValueError("Add at least one column divider inside the selected table.")
        box = (self.selection.left() / self.image.width(), self.selection.top() / self.image.height(),
               self.selection.right() / self.image.width(), self.selection.bottom() / self.image.height())
        return box, [x / self.image.width() for x in boundaries]


class PdfSelectionPanel(QWidget):
    run_requested = Signal(object, object, int, bool)

    def __init__(self):
        super().__init__(); self.path = None; self.password = None; self.document = None; self.page_index = 0
        root = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.prev = QPushButton("Previous page"); self.next = QPushButton("Next page"); self.page_label = QLabel("No PDF loaded")
        area = QPushButton("1. Draw table area"); divider = QPushButton("2. Add column divider"); undo = QPushButton("Undo divider")
        self.all_pages = QCheckBox("Apply column layout to all pages"); self.all_pages.setChecked(True)
        run = QPushButton("3. Run OCR on selection"); run.setObjectName("primary")
        controls.addWidget(self.prev); controls.addWidget(self.next); controls.addWidget(self.page_label); controls.addStretch()
        controls.addWidget(area); controls.addWidget(divider); controls.addWidget(undo); controls.addWidget(self.all_pages); controls.addWidget(run)
        self.canvas = SelectionCanvas(); root.addLayout(controls); root.addWidget(self.canvas, 1)
        tip = QLabel("Drag around the table, then click Add column divider and click each boundary between columns. Red guides show the OCR columns.")
        tip.setObjectName("muted"); root.addWidget(tip)
        self.prev.clicked.connect(lambda: self.show_page(self.page_index - 1)); self.next.clicked.connect(lambda: self.show_page(self.page_index + 1))
        area.clicked.connect(lambda: setattr(self.canvas, "mode", "area")); divider.clicked.connect(lambda: setattr(self.canvas, "mode", "divider"))
        undo.clicked.connect(self.undo_divider); run.clicked.connect(self.run_selection)

    def load_pdf(self, path: Path, password: str | None):
        import pypdfium2 as pdfium
        self.path, self.password = path, password
        self.document = pdfium.PdfDocument(str(path), password=password)
        self.show_page(0)

    def show_page(self, index: int):
        if not self.document or index < 0 or index >= len(self.document):
            return
        self.page_index = index
        pil = self.document[index].render(scale=1.7).to_pil().convert("RGBA")
        raw = pil.tobytes("raw", "RGBA")
        image = QImage(raw, pil.width, pil.height, pil.width * 4, QImage.Format_RGBA8888).copy()
        pil.close(); self.canvas.set_image(image)
        self.page_label.setText(f"Page {index + 1} of {len(self.document)}")
        self.prev.setEnabled(index > 0); self.next.setEnabled(index + 1 < len(self.document))

    def undo_divider(self):
        if self.canvas.dividers:
            self.canvas.dividers.pop(); self.canvas.update()

    def run_selection(self):
        try:
            box, boundaries = self.canvas.geometry()
            self.run_requested.emit(box, boundaries, self.page_index, self.all_pages.isChecked())
        except ValueError as exc:
            QMessageBox.warning(self, "Selection incomplete", str(exc))


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
        self._retry_guided = False
        self._guided_args = None
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
        self.table.horizontalHeader().sectionDoubleClicked.connect(self.rename_header)
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
        workspace = QWidget(); workspace_layout = QVBoxLayout(workspace); workspace_layout.setContentsMargins(0, 0, 0, 0); workspace_layout.addWidget(splitter)
        self.viewer = PdfSelectionPanel(); self.viewer.run_requested.connect(self.start_guided_ocr)
        self.tabs = QTabWidget(); self.tabs.addTab(self.viewer, "PDF viewer & column selection"); self.tabs.addTab(workspace, "Extracted data")
        outer.addWidget(self.tabs, 1)

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
        self.pdf_path = Path(name); self._pdf_password = None; self.settings.setValue("lastFolder", str(self.pdf_path.parent))
        try:
            self.viewer.load_pdf(self.pdf_path, None); self.tabs.setCurrentWidget(self.viewer)
        except Exception:
            pass  # Encrypted PDFs are loaded after the secure password prompt succeeds.
        self.start_extraction()

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
            if self._retry_guided and self._guided_args:
                self._retry_guided = False; self.start_guided_ocr(*self._guided_args)
            else:
                self.start_extraction()

    def extraction_done(self, result: ExtractionResult):
        self.extraction = result; self.model.replace(result.columns, result.rows)
        self.subtitle.setText(str(self.pdf_path)); self.statusBar().showMessage("Extraction complete. Review highlighted blanks and warnings.", 6000)
        self.refresh_report()
        try:
            if self.viewer.document is None and self.pdf_path:
                self.viewer.load_pdf(self.pdf_path, self._pdf_password)
        except Exception:
            pass
        self.tabs.setCurrentIndex(1)

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

    def start_guided_ocr(self, box, boundaries, page_index: int, all_pages: bool):
        if not self.pdf_path or self._thread:
            return
        self._guided_args = (box, boundaries, page_index, all_pages)
        self.progress.show(); self.reextract.setEnabled(False); self.statusBar().showMessage("Running OCR inside the selected table area…")
        thread = QThread(self)
        worker = GuidedOcrWorker(self.pdf_path, box, boundaries, page_index, all_pages, self._pdf_password)
        worker.moveToThread(thread); thread.started.connect(worker.run)
        worker.finished.connect(self.extraction_done); worker.failed.connect(self.extraction_failed); worker.password_required.connect(self._guided_password)
        worker.finished.connect(thread.quit); worker.failed.connect(thread.quit); worker.password_required.connect(thread.quit)
        thread.finished.connect(worker.deleteLater); thread.finished.connect(thread.deleteLater); thread.finished.connect(self._thread_finished)
        self._thread = thread; self._worker = worker; thread.start()

    def _guided_password(self, reason: str):
        self._retry_guided = True
        self.request_pdf_password(reason)

    def rename_header(self, section: int):
        old = self.model.columns[section]
        name, accepted = QInputDialog.getText(self, "Rename column", "Column name:", text=old)
        if accepted:
            try:
                self.model.rename_column(section, name); self.refresh_report()
            except ValueError as exc:
                QMessageBox.warning(self, "Cannot rename column", str(exc))

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
