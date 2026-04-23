"""
ui/file_panel_widget.py
Self-contained left-panel widget: file list, folder grouping, context menus,
collapse state, cut/paste clipboard, and Ctrl+A scoped selection.
"""

import logging
from pathlib import Path

from PyQt5.QtCore import Qt, QEvent, QObject, QPoint, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from core.folder_store import Folder, FolderStore
from core.paths import UNLABELED_DIR
from core.status import FileStatus, STATUS_COLORS, resolve_status
from ui.list_items import ROW_FILE, ROW_FOLDER, ROW_NO_FOLDER, make_list_item

log = logging.getLogger(__name__)

_UNLABELED_DIR = UNLABELED_DIR


class FilePanelWidget(QWidget):
    """
    Emits signals upward; MainWindow responds to them without reaching into
    the list internals.
    """

    file_selected          = pyqtSignal(Path)
    open_viewer_requested  = pyqtSignal(Path)
    move_paths_requested   = pyqtSignal(list, object)   # (paths, folder_id | None)
    cut_paths_requested    = pyqtSignal(list)            # paths

    def __init__(
        self,
        store:  FolderStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store              = store
        self._collapsed_folders: set[str] = set()
        self._cut_stems:         set[str] = set()
        self._build_ui()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def scan(self) -> None:
        """Rebuild the list from the unlabeled directory."""
        cur          = self._list.currentItem()
        selected_path: Path | None = (
            cur.data(Qt.UserRole)
            if cur and cur.data(Qt.UserRole + 2) == ROW_FILE
            else None
        )

        self._list.clear()

        try:
            all_dcm = sorted(
                f for f in _UNLABELED_DIR.iterdir()
                if f.suffix.lower() in (".dcm", ".dicom") and f.is_file()
            )
        except OSError:
            all_dcm = []

        if not all_dcm:
            self._count_label.setText("0 files")
            placeholder = QListWidgetItem(
                "No files yet.\nImport DICOMs or a .dcmpack to get started."
            )
            placeholder.setFlags(Qt.NoItemFlags)
            placeholder.setForeground(QColor("#2E3A50"))
            placeholder.setTextAlignment(Qt.AlignCenter)
            self._list.addItem(placeholder)
            return

        stem_to_path: dict[str, Path] = {f.stem: f for f in all_dcm}
        folders      = self._store.all_folders()
        assigned:    set[str] = set()
        restore_row: int      = -1

        for folder in folders:
            folder_paths = [stem_to_path[s] for s in folder.stems if s in stem_to_path]
            if not folder_paths:
                continue
            assigned.update(p.stem for p in folder_paths)
            self._add_folder_header_row(folder, folder_paths)
            if folder.id not in self._collapsed_folders:
                for p in folder_paths:
                    self._list.addItem(self._make_file_item(p))
                    if p == selected_path:
                        restore_row = self._list.count() - 1

        unassigned = [f for f in all_dcm if f.stem not in assigned]
        if unassigned:
            if folders:
                self._add_no_folder_header_row(unassigned)
            for p in unassigned:
                self._list.addItem(self._make_file_item(p))
                if p == selected_path:
                    restore_row = self._list.count() - 1

        if restore_row >= 0:
            self._list.setCurrentRow(restore_row)

        n = len(all_dcm)
        self._count_label.setText(f"{n} file{'s' if n != 1 else ''}")
        log.info("Scanned Unlabeled/ — %d file(s) found.", n)

    def refresh_item_status(self, dcm_path: Path) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.UserRole + 2) != ROW_FILE:
                continue
            if item.data(Qt.UserRole) == dcm_path:
                status = resolve_status(dcm_path, self._store)
                item.setForeground(QColor(STATUS_COLORS[status]))
                item.setData(Qt.UserRole + 1, status)
                break

    def set_cut_stems(self, stems: set[str]) -> None:
        self._cut_stems = stems
        self.scan()

    def clear_cut_stems(self) -> None:
        self._cut_stems.clear()

    @property
    def cut_stems(self) -> set[str]:
        return self._cut_stems

    def selected_file_paths(self) -> list[Path]:
        return [
            self._list.item(i).data(Qt.UserRole)
            for i in range(self._list.count())
            if self._list.item(i).isSelected()
            and self._list.item(i).data(Qt.UserRole + 2) == ROW_FILE
        ]

    def current_file_path(self) -> Path | None:
        item = self._list.currentItem()
        if item and item.data(Qt.UserRole + 2) == ROW_FILE:
            return item.data(Qt.UserRole)
        return None

    def select_next(self) -> None:
        row = self._list.currentRow()
        for i in range(row + 1, self._list.count()):
            if self._list.item(i).data(Qt.UserRole + 2) == ROW_FILE:
                self._list.setCurrentRow(i)
                return

    def select_prev(self) -> None:
        row = self._list.currentRow()
        for i in range(row - 1, -1, -1):
            if self._list.item(i).data(Qt.UserRole + 2) == ROW_FILE:
                self._list.setCurrentRow(i)
                return

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setStyleSheet("background-color: #0D1118;")
        self.setFixedWidth(340)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_panel_header())
        layout.addWidget(self._build_legend())

        self._list = QListWidget()
        self._list.setSpacing(2)
        self._list.setSelectionMode(QListWidget.ExtendedSelection)
        self._list.setStyleSheet(
            "QListWidget { background: #0D1118; border: none; outline: none; }"
            "QListWidget::item { padding: 10px 16px; border-bottom: 1px solid #111820; font-size: 12px; }"
            "QListWidget::item:selected { background: #1A3050; border-left: 3px solid #2A7AD4; color: #FFFFFF; }"
            "QListWidget::item:hover:!selected { background: #141D2E; }"
        )
        self._list.currentItemChanged.connect(self._on_current_changed)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        self._list.installEventFilter(self)
        layout.addWidget(self._list, stretch=1)

    def _build_panel_header(self) -> QWidget:
        ph = QWidget()
        ph.setFixedHeight(40)
        ph.setStyleSheet("background-color: #0A0F1A; border-bottom: 1px solid #1E2A3A;")
        layout = QHBoxLayout(ph)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.addWidget(self._section_label("DICOM FILES"))
        layout.addStretch()
        self._count_label = QLabel("0 files")
        self._count_label.setStyleSheet("color: #3E4A5C; font-size: 10px;")
        layout.addWidget(self._count_label)
        return ph

    def _build_legend(self) -> QWidget:
        legend = QWidget()
        legend.setStyleSheet("background-color: #080D14; border-bottom: 1px solid #1E2A3A;")
        layout = QHBoxLayout(legend)
        layout.setContentsMargins(16, 6, 16, 6)
        layout.setSpacing(14)
        for status, color in STATUS_COLORS.items():
            dot = QLabel("*")
            dot.setStyleSheet(f"color: {color}; font-size: 10px;")
            lbl = QLabel(status)
            lbl.setStyleSheet("color: #3E4A5C; font-size: 10px;")
            layout.addWidget(dot)
            layout.addWidget(lbl)
        layout.addStretch()
        return legend

    @staticmethod
    def _section_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "color: #5A7FA8; font-size: 10px; font-weight: 600; letter-spacing: 1.5px;"
        )
        return lbl

    # ------------------------------------------------------------------
    # List item builders
    # ------------------------------------------------------------------

    def _make_file_item(self, dcm_path: Path) -> QListWidgetItem:
        item = make_list_item(dcm_path, self._store)
        if dcm_path.stem in self._cut_stems:
            item.setForeground(QColor("#3E4A5C"))
            f = item.font()
            f.setItalic(True)
            item.setFont(f)
        return item

    def _add_folder_header_row(self, folder: Folder, paths: list[Path]) -> None:
        n_total   = len(paths)
        n_labeled = sum(
            1 for p in paths
            if resolve_status(p, self._store) == FileStatus.LABELED
        )
        collapsed = folder.id in self._collapsed_folders
        arrow     = "\u25b6" if collapsed else "\u25bc"
        suffix    = f"  \u00b7  {len(folder.mandatory_labels)} required" if folder.mandatory_labels else ""
        text      = f"{arrow}  {folder.name}    {n_labeled}\u202f/\u202f{n_total} labeled{suffix}"

        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemIsEnabled)
        item.setData(Qt.UserRole,     None)
        item.setData(Qt.UserRole + 1, None)
        item.setData(Qt.UserRole + 2, ROW_FOLDER)
        item.setData(Qt.UserRole + 3, folder.id)
        f = item.font()
        f.setBold(True)
        item.setFont(f)
        item.setForeground(QColor("#8AB0D0"))
        item.setBackground(QColor("#0A0F1A"))
        item.setToolTip(
            f"Folder: {folder.name}\n"
            f"Mandatory labels: {', '.join(folder.mandatory_labels) or 'none'}\n"
            "Click to collapse / expand  ·  Right-click for options"
        )
        self._list.addItem(item)

    def _add_no_folder_header_row(self, paths: list[Path]) -> None:
        n    = len(paths)
        item = QListWidgetItem(f"\u2014  Unassigned    {n} file{'s' if n != 1 else ''}")
        item.setFlags(Qt.ItemIsEnabled)
        item.setData(Qt.UserRole + 2, ROW_NO_FOLDER)
        f = item.font()
        f.setBold(True)
        item.setFont(f)
        item.setForeground(QColor("#3E4A5C"))
        item.setBackground(QColor("#0A0F1A"))
        self._list.addItem(item)

    # ------------------------------------------------------------------
    # Internal slots
    # ------------------------------------------------------------------

    def _on_current_changed(self, current: QListWidgetItem, _prev) -> None:
        if current and current.data(Qt.UserRole + 2) == ROW_FILE:
            self.file_selected.emit(current.data(Qt.UserRole))

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        if item.data(Qt.UserRole + 2) == ROW_FILE:
            self.open_viewer_requested.emit(item.data(Qt.UserRole))

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        if item.data(Qt.UserRole + 2) == ROW_FOLDER:
            folder_id = item.data(Qt.UserRole + 3)
            if folder_id in self._collapsed_folders:
                self._collapsed_folders.discard(folder_id)
            else:
                self._collapsed_folders.add(folder_id)
            self.scan()

    # ------------------------------------------------------------------
    # Event filter — Ctrl+A folder-scoped select-all
    # ------------------------------------------------------------------

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if (
            obj is self._list
            and event.type() == QEvent.KeyPress
            and event.key() == Qt.Key_A
            and event.modifiers() == Qt.ControlModifier
        ):
            self._select_all_in_folder()
            return True
        return super().eventFilter(obj, event)

    def _select_all_in_folder(self) -> None:
        cur              = self._list.currentItem()
        group_header_row = -1
        group_folder_id  = None

        if cur is not None:
            cur_type = cur.data(Qt.UserRole + 2)
            if cur_type == ROW_FOLDER:
                group_header_row = self._list.row(cur)
                group_folder_id  = cur.data(Qt.UserRole + 3)
            else:
                for i in range(self._list.row(cur), -1, -1):
                    item = self._list.item(i)
                    rt   = item.data(Qt.UserRole + 2)
                    if rt == ROW_FOLDER:
                        group_header_row = i
                        group_folder_id  = item.data(Qt.UserRole + 3)
                        break
                    elif rt == ROW_NO_FOLDER:
                        group_header_row = i
                        group_folder_id  = None
                        break

        self._list.clearSelection()
        in_group = (group_header_row == -1)

        for i in range(self._list.count()):
            item = self._list.item(i)
            rt   = item.data(Qt.UserRole + 2)
            if rt == ROW_FOLDER:
                in_group = (i == group_header_row)
            elif rt == ROW_NO_FOLDER:
                in_group = (group_folder_id is None and i == group_header_row)
            elif rt == ROW_FILE and in_group:
                item.setSelected(True)

    # ------------------------------------------------------------------
    # Context menus
    # ------------------------------------------------------------------

    def _on_context_menu(self, pos: QPoint) -> None:
        item = self._list.itemAt(pos)
        if item is None:
            return
        global_pos = self._list.mapToGlobal(pos)
        rt         = item.data(Qt.UserRole + 2)
        if rt == ROW_FILE:
            self._show_file_context_menu(item, global_pos)
        elif rt == ROW_FOLDER:
            self._show_folder_context_menu(item.data(Qt.UserRole + 3), global_pos)

    def _show_file_context_menu(self, item: QListWidgetItem, global_pos: QPoint) -> None:
        paths = self.selected_file_paths() if item.isSelected() else [item.data(Qt.UserRole)]
        n     = len(paths)
        menu  = QMenu(self)

        cut_act = menu.addAction(f"{'Cut ' + str(n) + ' items' if n > 1 else 'Cut'}\tCtrl+X")
        cut_act.triggered.connect(lambda: self.cut_paths_requested.emit(paths))

        menu.addSeparator()

        move_lbl  = f"Move {n} items to Folder" if n > 1 else "Move to Folder"
        move_menu = menu.addMenu(move_lbl)
        for folder in self._store.all_folders():
            act = move_menu.addAction(folder.name)
            act.triggered.connect(
                lambda _, fid=folder.id: self.move_paths_requested.emit(paths, fid)
            )
        if self._store.all_folders():
            move_menu.addSeparator()
        move_menu.addAction("(No Folder / Unassign)").triggered.connect(
            lambda: self.move_paths_requested.emit(paths, None)
        )

        assigned = [p for p in paths if self._store.folder_for_stem(p.stem)]
        if assigned:
            menu.addSeparator()
            na  = len(assigned)
            lbl = f"Remove {na} items from Folder" if na > 1 else "Remove from Folder"
            menu.addAction(f"{lbl}  Del").triggered.connect(
                lambda: self.move_paths_requested.emit(assigned, None)
            )

        if self._cut_stems:
            menu.addSeparator()
            nc = len(self._cut_stems)
            menu.addAction(
                f"Paste {nc} item{'s' if nc != 1 else ''} here\tCtrl+V"
            ).triggered.connect(lambda: self._paste_near(paths[0]))

        menu.exec_(global_pos)

    def _show_folder_context_menu(self, folder_id: str, global_pos: QPoint) -> None:
        try:
            folder = self._store.get_folder(folder_id)
        except Exception:
            return

        menu = QMenu(self)
        menu.addAction("Rename / Edit Labels").triggered.connect(
            lambda: self._emit_rename_folder(folder_id)
        )
        menu.addSeparator()
        menu.addAction("Export Folder as Pack...").triggered.connect(
            lambda: self._emit_export_folder(folder_id)
        )

        if self._cut_stems:
            menu.addSeparator()
            nc = len(self._cut_stems)
            menu.addAction(
                f"Paste {nc} item{'s' if nc != 1 else ''} into \"{folder.name}\"\tCtrl+V"
            ).triggered.connect(lambda: self._do_paste(folder_id))

        menu.addSeparator()
        menu.addAction("Delete Folder").triggered.connect(
            lambda: self._emit_delete_folder(folder_id)
        )
        menu.exec_(global_pos)

    # ------------------------------------------------------------------
    # Paste helpers (resolved locally since they only affect list state)
    # ------------------------------------------------------------------

    def _paste_near(self, dcm_path: Path) -> None:
        f = self._store.folder_for_stem(dcm_path.stem)
        self._do_paste(f.id if f else None)

    def _do_paste(self, folder_id: str | None) -> None:
        stems = list(self._cut_stems)
        paths = [_UNLABELED_DIR / f"{s}.dcm" for s in stems]
        self._cut_stems.clear()
        self.move_paths_requested.emit(paths, folder_id)

    # ------------------------------------------------------------------
    # Forwarded folder actions — MainWindow connects to these via signals.
    # We re-use move_paths_requested for delete/rename/export by emitting
    # dedicated one-shot lambdas; heavier actions stay in MainWindow.
    # ------------------------------------------------------------------

    def _emit_rename_folder(self, folder_id: str) -> None:
        # MainWindow listens via a direct connection set up at construction.
        self._rename_folder_requested(folder_id)

    def _emit_export_folder(self, folder_id: str) -> None:
        self._export_folder_requested(folder_id)

    def _emit_delete_folder(self, folder_id: str) -> None:
        self._delete_folder_requested(folder_id)

    # Slots injected by MainWindow at construction time.
    def _rename_folder_requested(self, folder_id: str) -> None: ...
    def _export_folder_requested(self, folder_id: str) -> None: ...
    def _delete_folder_requested(self, folder_id: str) -> None: ...

    def bind_folder_actions(
        self,
        on_rename: callable,
        on_export: callable,
        on_delete: callable,
    ) -> None:
        """Called once by MainWindow to wire folder action callbacks."""
        self._rename_folder_requested = on_rename
        self._export_folder_requested = on_export
        self._delete_folder_requested = on_delete