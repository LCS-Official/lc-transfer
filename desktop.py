"""LC 互传: native Windows client for an Android SFTP service."""
from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
import math
import posixpath
import sys
import tempfile
import time
from collections import OrderedDict
from dataclasses import replace
from datetime import datetime
from pathlib import Path

if getattr(sys, 'frozen', False) and '--smoke-test' in sys.argv:
    import faulthandler
    _smoke_log = open(Path(sys.executable).with_suffix('.smoke.log'), 'w', encoding='utf-8')
    faulthandler.dump_traceback_later(15, file=_smoke_log)

import paramiko
from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSpinBox, QStyle, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from transfer_core import CancelEvent, Cancelled, Entry, Phone, Settings, UnknownHost, data_dir, human_size
from transfer_history import History
from image_preview import ImagePreview
from discovery import discover, identity, is_lan_address, key_fingerprint, pairing_code

APP_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
PAGE_SIZE = 60

STYLE = '''
QWidget { font-family: "Microsoft YaHei UI", "Segoe UI"; font-size: 13px; color: #24363b; }
QMainWindow, QDialog { background: #f5f8f8; }
QFrame#sidebar { background: #eaf1f1; border: none; }
QLabel#brand { font-size: 29px; font-weight: 700; color: #157e80; }
QLabel#title { font-size: 23px; font-weight: 700; }
QLabel#muted { color: #74878c; }
QLabel#date { font-size: 16px; font-weight: 600; color: #33434b; background: #e7ebed;
              padding: 9px 12px; margin: 8px 0; border-bottom: 1px solid #d6dfe2; border-radius: 4px; }
QPushButton { border: 1px solid #d6e2e3; border-radius: 9px; padding: 9px 16px; background: white; }
QPushButton:hover { background: #e2f2f1; border-color: #9acac8; }
QPushButton:disabled { color: #a6b5b9; background: #f2f6f6; }
QPushButton#primary { color: white; background: #239b73; border: none; font-weight: 700; }
QPushButton#primary:hover { background: #1c805f; }
QPushButton#primary:disabled { color: #f5fbf8; background: #afd6c7; }
QPushButton#danger { color: white; background: #d94b4b; border: none; font-weight: 700; }
QPushButton#danger:hover { background: #bc3838; }
QPushButton#danger:disabled { color: #fff7f7; background: #e9b5b5; }
QPushButton#nav { text-align: left; border: none; padding: 13px 18px; background: transparent; font-size: 15px; }
QPushButton#nav:checked { background: #d0eae7; color: #126d6d; font-weight: 600; }
QLineEdit, QSpinBox, QComboBox { border: 1px solid #d6e2e3; border-radius: 7px; padding: 8px; background: white; }
QLineEdit:focus { border-color: #229e9b; }
QScrollArea { border: 1px solid #e2ebeb; border-radius: 12px; background: white; }
QListWidget { background: white; border: none; outline: none; }
QListWidget::item { border: 2px solid transparent; border-radius: 9px; padding: 4px; }
QListWidget::item:selected { background: #e0f3f0; border-color: #55b8b1; color: #155858; }
QListWidget::item:hover { background: #f0f7f6; }
QTreeWidget { background: white; alternate-background-color: #f0f6f6; color: #24363b;
              border: 1px solid #d6e2e3; border-radius: 7px;
              selection-background-color: #d0eae7; selection-color: #124f50; }
QTreeWidget::item { padding: 5px 4px; }
QTreeWidget::item:hover { background: #e9f4f3; }
QTreeWidget::item:selected { background: #d0eae7; color: #124f50; }
QHeaderView { background: #eaf1f1; }
QHeaderView::section { background: #eaf1f1; color: #24363b; font-weight: 600; padding: 8px 6px;
                       border: none; border-right: 1px solid #d6e2e3; border-bottom: 1px solid #d6e2e3; }
QProgressBar { border: none; background: #dfeceb; border-radius: 4px; height: 7px; text-align: center; }
QProgressBar::chunk { background: #35aaa2; border-radius: 4px; }
'''


def icon_for(kind):
    colors = {'folder': '#eac75c', 'image': '#57b9bd', 'video': '#9a85d8',
              'audio': '#e583a6', 'document': '#639fca', 'other': '#8fa3a8'}
    labels = {'folder': '文件夹', 'image': '图片', 'video': '视频', 'audio': '音乐',
              'document': '文档', 'other': '文件'}
    p = QPixmap(144, 96)
    p.fill(Qt.GlobalColor.transparent)
    painter = QPainter(p)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colors[kind]))
    if kind == 'folder':
        painter.drawRoundedRect(32, 20, 37, 17, 5, 5)
        painter.drawRoundedRect(26, 31, 92, 55, 7, 7)
    else:
        painter.drawRoundedRect(37, 8, 70, 81, 9, 9)
        painter.setPen(QColor('white'))
        painter.setFont(QFont('Microsoft YaHei UI', 13, QFont.Weight.Bold))
        painter.drawText(p.rect(), Qt.AlignmentFlag.AlignCenter, labels[kind])
    painter.end()
    return QIcon(p)


class Signals(QObject):
    done = Signal(object)
    error = Signal(object)
    progress = Signal(str, int, int)
    thumbnail = Signal(str, str)


class Task(QRunnable):
    def __init__(self, work):
        super().__init__()
        self.signals = Signals()
        self.work = work

    def run(self):
        try:
            result = self.work(self.signals)
        except Exception as ex:
            self.signals.error.emit(ex)
        else:
            self.signals.done.emit(result)


class ConnectDialog(QDialog):
    def __init__(self, settings, password, parent):
        super().__init__(parent)
        self.setWindowTitle('连接 安卓手机')
        self.setMinimumWidth(550)
        layout = QVBoxLayout(self)
        description = QLabel('新版手机端优先局域网自动发现。跨网络时可填写自己的备用地址；首次连接需要双方确认。')
        description.setWordWrap(True)
        layout.addWidget(description)
        form = QFormLayout()
        self.host = QLineEdit(settings.host)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(settings.port)
        self.fallback = QLineEdit(settings.fallback_host)
        self.fallback.setPlaceholderText('可选：你自己的 VPN / 虚拟网络手机地址')
        self.fallback_port = QSpinBox()
        self.fallback_port.setRange(1, 65535)
        self.fallback_port.setValue(settings.fallback_port)
        self.user = QLineEdit(settings.username)
        self.password = QLineEdit(password)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.root = QLineEdit(settings.root)
        self.root.setToolTip('普通文件系统使用 /storage/emulated/0；SAF 使用 /。')
        self.download = QLineEdit(settings.download_dir)
        dl_row = QHBoxLayout()
        dl_row.addWidget(self.download)
        pick = QPushButton('选择')
        pick.clicked.connect(self.pick_dir)
        dl_row.addWidget(pick)
        for label, field in [('手机地址（手动连接）', self.host), ('SFTP 端口', self.port),
                             ('备用手机地址（可选）', self.fallback), ('备用 SFTP 端口', self.fallback_port),
                             ('用户名', self.user), ('密码（仅本次运行）', self.password),
                             ('共享根目录', self.root)]:
            form.addRow(label, field)
        form.addRow('电脑接收目录', dl_row)
        layout.addLayout(form)
        self.clear_pin = QCheckBox('清除已信任的手机指纹（仅更换设备或重新安装手机端时使用）')
        if settings.fingerprint:
            pin = QLabel('已信任：' + settings.fingerprint)
            pin.setWordWrap(True)
            pin.setObjectName('muted')
            layout.addWidget(pin)
            layout.addWidget(self.clear_pin)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('连接手机')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.original = settings
        self.auto_connect = False
        automatic = QPushButton('保存设置并自动发现（新版手机端）')
        automatic.setObjectName('primary')
        automatic.clicked.connect(self.choose_automatic)
        layout.insertWidget(layout.count() - 1, automatic)

    def choose_automatic(self):
        self.auto_connect = True
        self.accept()

    def pick_dir(self):
        folder = QFileDialog.getExistingDirectory(self, '电脑接收目录', self.download.text())
        if folder:
            self.download.setText(folder)

    def result_settings(self):
        host = self.host.text().strip()
        port = self.port.value()
        fingerprint = self.original.fingerprint
        if (host, port) != (self.original.host, self.original.port) or self.clear_pin.isChecked():
            fingerprint = ''
        return replace(self.original, host=host, port=port, username=self.user.text().strip(),
                       root=self.root.text().strip() or '/storage/emulated/0',
                       download_dir=self.download.text().strip() or self.original.download_dir, fingerprint=fingerprint,
                       fallback_host=self.fallback.text().strip(), fallback_port=self.fallback_port.value())


class Gallery(QListWidget):
    def __init__(self, entries, icons, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setIconSize(QSize(144, 96))
        self.setGridSize(QSize(172, 154))
        self.setSpacing(3)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        for entry in entries:
            subtitle = '文件夹' if entry.is_dir else human_size(entry.size)
            item = QListWidgetItem(icons[entry.kind], f'{entry.name}\n{subtitle}')
            item.setData(Qt.ItemDataRole.UserRole, entry)
            date = datetime.fromtimestamp(entry.mtime).strftime('%Y-%m-%d %H:%M:%S')
            item.setToolTip(f'{entry.path}\n修改时间：{date}\n{subtitle}')
            self.addItem(item)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cols = max(1, self.viewport().width() // (self.gridSize().width() + self.spacing()))
        self.setFixedHeight(math.ceil(self.count() / cols) * (self.gridSize().height() + self.spacing()) + 12)


STATE_NAMES = {'running': '正在传输', 'done': '已完成', 'packed': '已写入 ZIP',
               'error': '失败', 'cancelled': '已取消', 'interrupted': '已中断', 'discarded': 'ZIP 未完成'}


class TransferDialog(QDialog):
    def __init__(self, history, parent):
        super().__init__(parent)
        self.history = history
        self.setWindowTitle('传输 · 正在进行与历史记录')
        self.resize(1000, 620)
        self.setMinimumSize(700, 430)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('单个文件直接保存；多选或文件夹打包 ZIP。展开任务可查看每个文件的进度。'))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['文件 / 任务', '方向', '状态', '进度', '接收位置 / 错误'])
        self.tree.setColumnWidth(0, 290)
        self.tree.setColumnWidth(1, 110)
        self.tree.setColumnWidth(2, 90)
        self.tree.setColumnWidth(3, 165)
        self.tree.setAlternatingRowColors(True)
        layout.addWidget(self.tree, 1)
        close = QPushButton('关闭窗口（传输继续）')
        close.clicked.connect(self.close)
        layout.addWidget(close)
        self.nodes = {}
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)
        self.refresh()

    def refresh(self):
        for job in self.history.snapshot()['jobs']:
            task_id = job['id']
            if task_id not in self.nodes:
                node = QTreeWidgetItem()
                self.tree.addTopLevelItem(node)
                self.nodes[task_id] = node
                node.setExpanded(job['state'] == 'running')
            node = self.nodes[task_id]
            direction = '电脑 → 手机' if job['direction'] == 'upload' else '手机 → 电脑'
            started = datetime.fromtimestamp(job['started']).strftime('%m-%d %H:%M:%S')
            names = '、'.join(job['selected'][:2])
            title = f'{started} · {names}' + (f' 等 {len(job["selected"])} 项' if len(job['selected']) > 2 else '')
            if job.get('archive'):
                title += ' · ' + Path(job['archive']).name
            complete = sum(f['state'] in {'done', 'packed'} for f in job['files'])
            values = [title, direction, STATE_NAMES.get(job['state'], job['state']),
                      f'{complete} / {len(job["files"])} 个文件',
                      job.get('error') or job.get('archive') or job['target']]
            for column, text in enumerate(values):
                node.setText(column, text)
                node.setToolTip(column, text)
            for item in job['files']:
                key = (task_id, item['path'])
                if key not in self.nodes:
                    child = QTreeWidgetItem(node)
                    self.nodes[key] = child
                child = self.nodes[key]
                values = [item['name'], direction, STATE_NAMES.get(item['state'], item['state']),
                          f'{human_size(item["done"])} / {human_size(item["size"])}', item['target']]
                for column, text in enumerate(values):
                    child.setText(column, text)
                    child.setToolTip(column, text)

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start(500)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)


class Window(QMainWindow):
    def __init__(self, auto_discover=True):
        super().__init__()
        self.setAcceptDrops(True)
        self.setWindowTitle('LC 互传 · 电脑 ↔ 安卓')
        self.resize(1280, 850)
        self.setMinimumSize(950, 650)
        self.settings = Settings.load()
        self.password = ''
        self.history = History()
        self.history.bind_device(self.settings.host, self.settings.fingerprint)
        self.transfer_dialog = None
        self.image_preview = None
        self.connected = False
        self.control = None
        self.computer_key = None
        self.auto_enabled = auto_discover
        self.connecting_auto = False
        self.auto_candidates = []
        self.lan_check_at = 0.0
        self.maintenance_running = False
        self.share_retry_at = 0.0
        self.closing = False
        self.retry_at = 0.0
        self.discovery_pool = QThreadPool(self)
        self.discovery_pool.setMaxThreadCount(1)
        self.busy = False
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        # Keep signal owners alive until their queued UI callbacks have run.
        self.tasks = set()
        self.cancel = CancelEvent()
        self.thumb_cancel = CancelEvent()
        self.entries = []
        self.index = None
        self.page = 0
        self.category = 'files'
        self.current_path = self.settings.root
        self.galleries = []
        self.thumbnail_generation = 0
        self.scroll_entries = []
        self.scroll_loaded = 0
        self.scroll_batches = []
        self.batch_loading = False
        self.thumbnail_items = {}
        self.icons = {k: icon_for(k) for k in ['folder', 'image', 'video', 'audio', 'document', 'other']}
        self.setWindowIcon(self.icons['folder'])
        shell = QWidget()
        self.setCentralWidget(shell)
        outer = QHBoxLayout(shell)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(195)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 32, 18, 22)
        brand = QLabel('LC 互传')
        brand.setObjectName('brand')
        side.addWidget(brand)
        subtitle = QLabel('电脑与安卓\n局域网优先 · 虚拟网络备用')
        subtitle.setObjectName('muted')
        side.addWidget(subtitle)
        side.addSpacing(30)
        self.nav = {}
        for key, name in [('recent', '最近 30 天'), ('image', '图片'),
                          ('video', '视频'), ('audio', '音乐'),
                          ('document', '文档 / 压缩包'), ('files', '手机文件')]:
            button = QPushButton(name)
            button.setObjectName('nav')
            button.setIcon(self.icons.get(key, self.icons['folder']))
            button.setIconSize(QSize(25, 25))
            button.setCheckable(True)
            button.setChecked(key == 'files')
            button.clicked.connect(lambda checked=False, k=key: self.choose_category(k))
            self.nav[key] = button
            side.addWidget(button)
        side.addStretch()
        hint = QLabel('目标手机\n安卓手机\n' + self.settings.host)
        hint.setObjectName('muted')
        side.addWidget(hint)
        self.target_hint = hint
        help_button = QPushButton('手机安装与设置')
        help_button.clicked.connect(self.open_help)
        side.addWidget(help_button)
        manual = QPushButton('连接设置 / 旧版兼容')
        manual.clicked.connect(self.show_connection)
        side.addWidget(manual)
        outer.addWidget(sidebar)
        main = QVBoxLayout()
        main.setContentsMargins(26, 24, 26, 18)
        main.setSpacing(12)
        outer.addLayout(main, 1)
        top = QHBoxLayout()
        self.folder_button = QPushButton('发送文件夹')
        self.folder_button.clicked.connect(self.upload_folder)
        self.download_button = QPushButton('↓  传输到电脑')
        self.download_button.setObjectName('primary')
        self.download_button.setToolTip('单个文件直接下载；多选文件或文件夹打包 ZIP。')
        self.download_button.clicked.connect(self.download_selected)
        self.refresh_button = QPushButton('刷新')
        self.refresh_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.refresh_button.clicked.connect(self.refresh)
        self.delete_button = QPushButton('删除')
        self.delete_button.setObjectName('danger')
        self.delete_button.setToolTip('永久删除选中的手机文件；不删除文件夹。')
        self.delete_button.clicked.connect(self.delete_selected)
        for button in [self.folder_button, self.download_button, self.refresh_button, self.delete_button]:
            top.addWidget(button)
        top.addStretch()
        transfers_button = QPushButton('传输')
        transfers_button.clicked.connect(self.show_transfers)
        top.addWidget(transfers_button)
        self.connect_button = QPushButton('连接手机')
        self.connect_button.clicked.connect(self.toggle_connection)
        top.addWidget(self.connect_button)
        main.addLayout(top)
        heading = QHBoxLayout()
        self.title = QLabel('手机文件')
        self.title.setObjectName('title')
        heading.addWidget(self.title)
        heading.addStretch()
        self.search = QLineEdit()
        self.search.setAcceptDrops(False)
        self.search.setPlaceholderText('搜索文件名或路径')
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(270)
        self.search.textChanged.connect(self.reset_page)
        heading.addWidget(self.search)
        self.sort = QComboBox()
        self.sort.addItems(['按时间降序', '按时间升序', '按名称排序'])
        self.sort.currentIndexChanged.connect(self.reset_page)
        heading.addWidget(self.sort)
        main.addLayout(heading)
        path_row = QHBoxLayout()
        self.up = QPushButton('上一级')
        self.up.clicked.connect(self.go_up)
        path_row.addWidget(self.up)
        self.path = QLineEdit(self.current_path)
        self.path.setAcceptDrops(False)
        self.path.returnPressed.connect(lambda: self.load_folder(self.path.text()))
        path_row.addWidget(self.path, 1)
        self.root_button = QPushButton('存储根目录')
        self.root_button.clicked.connect(lambda: self.load_folder(self.settings.root))
        path_row.addWidget(self.root_button)
        main.addLayout(path_row)
        self.path_row_widgets = [self.up, self.path, self.root_button]
        controls = QHBoxLayout()
        self.selection = QLabel('尚未连接')
        self.selection.setObjectName('muted')
        controls.addWidget(self.selection)
        controls.addStretch()
        self.all_button = QPushButton('全选本页')
        self.all_button.clicked.connect(self.select_all)
        controls.addWidget(self.all_button)
        clear = QPushButton('取消选择')
        clear.clicked.connect(lambda: [g.clearSelection() for g in self.galleries])
        controls.addWidget(clear)
        main.addLayout(controls)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setToolTip('拖入电脑上的文件或文件夹，松开即可上传到手机接收目录。')
        self.scroll.verticalScrollBar().valueChanged.connect(self.maybe_load_more)
        self.scroll.verticalScrollBar().rangeChanged.connect(
            lambda *_: QTimer.singleShot(0, self.maybe_load_more))
        main.addWidget(self.scroll, 1)
        paging = QHBoxLayout()
        self.previous = QPushButton('上一页')
        self.previous.clicked.connect(lambda: self.change_page(-1))
        self.next = QPushButton('下一页')
        self.next.clicked.connect(lambda: self.change_page(1))
        self.page_label = QLabel()
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        paging.addWidget(self.previous)
        paging.addWidget(self.page_label, 1)
        paging.addWidget(self.next)
        main.addLayout(paging)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        main.addWidget(self.progress)
        status_row = QHBoxLayout()
        self.status = QLabel('正在查找手机 · 局域网优先，虚拟网络备用。')
        self.status.setWordWrap(True)
        self.status.setObjectName('muted')
        status_row.addWidget(self.status, 1)
        self.cancel_button = QPushButton('取消操作')
        self.cancel_button.clicked.connect(self.cancel_current)
        self.cancel_button.hide()
        status_row.addWidget(self.cancel_button)
        main.addLayout(status_row)
        self.destination = QPushButton('电脑接收目录：' + self.settings.download_dir)
        self.destination.clicked.connect(self.open_downloads)
        main.addWidget(self.destination)
        self.render()
        self.update_controls()
        self.discovery_timer = QTimer(self)
        self.discovery_timer.timeout.connect(self.auto_tick)
        if auto_discover:
            self.discovery_timer.start(3000)
            QTimer.singleShot(400, self.auto_tick)

    def disconnect_phone(self):
        self.connected = False
        if self.control:
            self.control.__exit__()
            self.control = None
        self.thumb_cancel.set()
        self.index = None
        self.entries = []
        self.render()
        self.update_controls()

    def toggle_connection(self):
        if self.connected:
            self.auto_enabled = False
            self.disconnect_phone()
            self.status.setText('已断开连接 · 点击“自动连接手机”可重新发现。')
        else:
            self.auto_enabled = True
            self.retry_at = 0
            self.discovery_timer.start(3000)
            self.auto_tick()

    def auto_tick(self):
        if self.closing or self.maintenance_running:
            return
        if self.control:
            control = self.control
            settings = replace(self.settings)
            check_lan = (self.auto_enabled and not self.busy and not self.pool.activeThreadCount() and
                         time.monotonic() >= self.lan_check_at and not is_lan_address(settings.host))
            if check_lan:
                self.lan_check_at = time.monotonic() + 15
            check_share = (settings.key_auth and not self.busy and not self.pool.activeThreadCount()
                           and time.monotonic() >= self.share_retry_at)
            self.maintenance_running = True
            def heartbeat(signals):
                control.heartbeat()
                if check_share:
                    request = control.pending_share()
                    if request:
                        return request
                if check_lan:
                    for phone in discover(settings.host, self.computer_key or identity(), .7, lan_only=True):
                        if not phone['paired'] or phone['fingerprint'] != settings.fingerprint:
                            continue
                        candidate = replace(settings, host=phone['host'], port=phone['port'])
                        replacement = Phone(candidate, '')
                        try:
                            replacement.__enter__()
                            replacement.sftp.get_channel().settimeout(5)
                            replacement.heartbeat()
                            return replacement, candidate, phone['name']
                        except (OSError, EOFError, ValueError, paramiko.SSHException):
                            replacement.__exit__()
                return None
            task = Task(heartbeat)
            self.tasks.add(task)
            def alive(result):
                self.tasks.discard(task)
                self.maintenance_running = False
                if isinstance(result, dict):
                    if self.control is control and not self.closing and not self.busy:
                        self.receive_shared(result)
                elif result:
                    replacement, candidate, name = result
                    if (self.control is control and not self.closing and not self.busy and
                            not self.pool.activeThreadCount()):
                        self.control = replacement
                        self.settings = candidate
                        self.settings.save()
                        control.__exit__()
                        self.target_hint.setText('局域网 · ' + name + '\n' + candidate.host)
                        self.status.setText('已切换到局域网 · ' + candidate.host)
                    else:
                        replacement.__exit__()
            def lost(error):
                self.tasks.discard(task)
                self.maintenance_running = False
                if self.control is control:
                    self.disconnect_phone()
                    self.retry_at = time.monotonic() + 5
                    self.status.setText('手机连接已断开，正在等待重新发现…')
            task.signals.done.connect(alive)
            task.signals.error.connect(lost)
            self.discovery_pool.start(task)
            return
        if self.connected or self.busy or not self.auto_enabled or time.monotonic() < self.retry_at:
            return
        self.maintenance_running = True
        host = self.settings.host
        def scan(signals):
            key = self.computer_key or identity()
            return key, discover(host, key, fallback_host=self.settings.fallback_host)
        task = Task(scan)
        self.tasks.add(task)
        def found(result):
            self.tasks.discard(task)
            self.maintenance_running = False
            if self.closing or self.busy or not self.auto_enabled or self.connected:
                return
            self.computer_key, phones = result
            phones = [phone for phone in phones if not self.settings.fingerprint or
                      phone['fingerprint'] == self.settings.fingerprint]
            if (self.settings.fallback_host and self.settings.key_auth and self.settings.fingerprint
                    and not any(p['host'] == self.settings.fallback_host for p in phones)):
                phones.append({'host': self.settings.fallback_host, 'port': self.settings.fallback_port, 'name': '已配对手机',
                               'paired': True, 'fingerprint': self.settings.fingerprint})
            if not phones:
                self.status.setText('正在查找手机 · 请打开手机互传，并连接同一局域网或虚拟网络。')
                return
            for phone in phones:
                if self.settings.fingerprint and self.settings.fingerprint != phone['fingerprint']:
                    continue
                code = pairing_code(phone['fingerprint'], key_fingerprint(self.computer_key))
                self.target_hint.setText('发现手机\n' + phone['name'] + '\n' + phone['host'])
                if not phone['paired']:
                    self.status.setText(f'发现 {phone["name"]} · 核对码 {code} · 请在手机上允许这台电脑连接。')
                    return
                self.auto_candidates = [other for other in phones if other is not phone and other['paired']
                                        and other['fingerprint'] == phone['fingerprint']]
                self.connect_candidate(phone)
                return
            self.status.setText('发现手机，但身份与已保存的设备不同。确认重新安装后，可在连接设置中清除旧指纹。')
        def failed(error):
            self.tasks.discard(task)
            self.maintenance_running = False
            if not self.closing:
                logging.warning('discovery failed: %s: %s', type(error).__name__, error)
                self.status.setText('自动发现暂不可用：' + str(error))
        task.signals.done.connect(found)
        task.signals.error.connect(failed)
        self.discovery_pool.start(task)

    def connect_candidate(self, phone):
        self.settings.host = phone['host']
        self.settings.port = phone['port']
        self.settings.username = 'user'
        self.settings.root = '/storage/emulated/0'
        self.settings.key_auth = True
        self.password = ''
        self.connecting_auto = True
        route = '局域网' if is_lan_address(phone['host']) else '虚拟网络'
        self.target_hint.setText(route + ' · ' + phone['name'] + '\n' + phone['host'])
        self.connect_phone()

    def open_help(self):
        help_file = APP_DIR / '手机设置.html'
        if help_file.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(help_file)))
        else:
            QDesktopServices.openUrl(QUrl('https://github.com/wolpi/prim-ftpd'))

    def show_transfers(self):
        if self.transfer_dialog is None:
            self.transfer_dialog = TransferDialog(self.history, self)
        self.transfer_dialog.show()
        self.transfer_dialog.raise_()
        self.transfer_dialog.activateWindow()

    def open_downloads(self):
        path = Path(self.settings.download_dir)
        try:
            path.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except OSError as ex:
            QMessageBox.warning(self, '无法打开接收目录', str(ex))

    def update_controls(self):
        available = self.connected and not self.busy
        for w in [self.folder_button, self.refresh_button, self.all_button, self.search, self.sort, *self.nav.values()]:
            w.setEnabled(available)
        for w in self.path_row_widgets:
            w.setEnabled(available and self.category == 'files')
        self.up.setEnabled(available and self.category == 'files' and self.current_path != self.settings.root)
        selected = self.selected()
        self.download_button.setEnabled(available and bool(selected))
        self.delete_button.setEnabled(available and bool(selected) and not any(e.is_dir for e in selected))
        self.connect_button.setEnabled(not self.busy)
        self.connect_button.setText('断开连接' if self.connected else '自动连接手机')
        name = '' if self.connected else 'primary'
        if self.connect_button.objectName() != name:
            self.connect_button.setObjectName(name)
            self.connect_button.style().unpolish(self.connect_button)
            self.connect_button.style().polish(self.connect_button)
        self.scroll.setEnabled(not self.busy)
        self.previous.setEnabled(available and self.page > 0)
        self.next.setEnabled(available and (self.page + 1) * PAGE_SIZE < len(self.filtered()))

    def selected(self):
        return [item.data(Qt.ItemDataRole.UserRole) for g in self.galleries for item in g.selectedItems()]

    def selection_changed(self):
        selected = self.selected()
        count = len(self.filtered())
        self.selection.setText(f'{count:,} 项 · 已选择 {len(selected)} 项' if self.connected else '尚未连接')
        self.download_button.setEnabled(self.connected and not self.busy and bool(selected))
        self.delete_button.setEnabled(self.connected and not self.busy and bool(selected)
                                      and not any(e.is_dir for e in selected))

    def select_all(self):
        for gallery in self.galleries:
            gallery.selectAll()

    def cancel_current(self):
        self.cancel.set()
        self.thumb_cancel.set()
        if self.connecting_auto or not self.connected:
            self.auto_enabled = False
            self.auto_candidates = []
        self.status.setText('正在中断当前操作…')
        self.cancel_button.setEnabled(False)

    def start_task(self, work, done, message, on_error=None):
        self.thumb_cancel.set()
        self.cancel = CancelEvent()
        cancel = self.cancel
        self.busy = True
        self.status.setText(message)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self.update_controls()
        task = Task(work)
        self.tasks.add(task)

        def finished(result):
            if cancel.is_set():
                failed(Cancelled('操作已取消，已完成的文件会保留。'))
                return
            try:
                self.end_task()
                done(result)
            finally:
                self.tasks.discard(task)

        def failed(error):
            if cancel.is_set():
                error = Cancelled('操作已取消，已完成的文件会保留。')
            self.tasks.discard(task)
            if on_error is None:
                self.handle_error(error)
            else:
                self.end_task()
                on_error(error)

        task.signals.done.connect(finished)
        task.signals.error.connect(failed)
        task.signals.progress.connect(self.show_progress)
        self.pool.start(task)
        return task

    def end_task(self):
        self.busy = False
        self.progress.hide()
        self.cancel_button.hide()
        self.update_controls()
        if not self.cancel.is_set():
            QTimer.singleShot(0, self.resume_thumbnails)

    def show_progress(self, message, done, total):
        if self.cancel.is_set():
            return
        self.status.setText(message)
        if total > 0:
            self.progress.setRange(0, 1000)
            self.progress.setValue(min(1000, int(done / total * 1000)))
        else:
            self.progress.setRange(0, 0)

    def handle_error(self, error):
        self.end_task()
        if isinstance(error, Cancelled):
            self.connecting_auto = False
            self.auto_candidates = []
            self.status.setText(str(error) if self.connected else '已取消连接 · 点击“自动连接手机”可重新连接。')
            return
        if isinstance(error, UnknownHost):
            self.auto_enabled = False
            box = QMessageBox(self)
            box.setWindowTitle('首次连接 · 确认手机身份')
            box.setIcon(QMessageBox.Icon.Question)
            if self.settings.key_auth and self.computer_key:
                code = pairing_code(error.fingerprint, key_fingerprint(self.computer_key))
                box.setText(f'核对码：{code}\n请确认与手机刚才显示的核对码一致。确认后将自动记住手机。')
            else:
                box.setText('请与手机端显示的 SSH 主机密钥指纹核对，确认后保存信任。')
            box.setInformativeText(f'{self.settings.host}:{self.settings.port}\n\n{error.fingerprint}')
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            if box.exec() == QMessageBox.StandardButton.Yes:
                self.settings.fingerprint = error.fingerprint
                self.settings.save()
                self.auto_enabled = self.settings.key_auth
                self.connect_phone()
            else:
                self.status.setText('尚未信任手机指纹，连接已取消。')
            return
        if self.connecting_auto:
            logging.warning('automatic connection to %s failed: %s: %s', self.settings.host, type(error).__name__, error)
            if self.auto_candidates:
                self.connect_candidate(self.auto_candidates.pop(0))
                return
            self.connecting_auto = False
            self.disconnect_phone()
            self.retry_at = time.monotonic() + 10
            self.status.setText('等待手机连接：' + str(error).splitlines()[0])
            return
        if isinstance(error, paramiko.AuthenticationException):
            message = '账号或密码不正确。请核对手机 Primitive FTPd 设置，并关闭匿名登录。'
        elif isinstance(error, (TimeoutError, ConnectionError, paramiko.ssh_exception.NoValidConnectionsError)):
            message = '无法连接手机。请确认同一局域网或虚拟网络可用、手机互传已启动，IP 和端口正确。\n\n' + str(error)
        else:
            message = str(error)
        self.status.setText('操作失败：' + message.splitlines()[0])
        logging.warning('operation failed: %s: %s', type(error).__name__, error)
        QMessageBox.warning(self, '操作未完成', message)

    def show_connection(self):
        previous_auto = self.auto_enabled
        self.auto_enabled = False
        dialog = ConnectDialog(self.settings, self.password, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.auto_enabled = previous_auto
            return
        settings = dialog.result_settings()
        if dialog.auto_connect:
            self.disconnect_phone()
            self.settings = settings
            self.settings.save()
            self.destination.setText('电脑接收目录：' + settings.download_dir)
            self.auto_enabled = True
            self.retry_at = 0
            self.discovery_timer.start(3000)
            self.auto_tick()
            return
        if not settings.host or not settings.username or not dialog.password.text():
            QMessageBox.warning(self, '连接信息不完整', '请填写手机 IP、用户名和密码。')
            return
        self.thumb_cancel.set()
        self.disconnect_phone()
        self.index = None
        self.entries = []
        self.settings = settings
        self.settings.save()
        self.password = dialog.password.text()
        self.destination.setText('电脑接收目录：' + settings.download_dir)
        self.target_hint.setText('目标手机\n安卓手机\n' + settings.host)
        self.connect_phone()

    def connect_phone(self):
        settings, password = replace(self.settings), self.password
        def work(signals):
            phone = Phone(settings, password, self.cancel)
            phone.__enter__()
            try:
                result = phone.list_dir(phone.root)
                receive = phone.device_config() if settings.key_auth else settings.receive_dir
                if settings.key_auth:
                    phone.sftp.get_channel().settimeout(5)
                    phone.heartbeat()
                else:
                    phone.__exit__()
                return result, phone if settings.key_auth else None, receive
            except BaseException:
                phone.__exit__()
                raise

        def done(result):
            result, self.control, receive = result
            self.connected = True
            self.connecting_auto = False
            self.auto_candidates = []
            self.lan_check_at = time.monotonic() + 15
            self.settings.receive_dir = receive
            self.settings.root = result[0]
            self.settings.save()
            self.current_path = result[0]
            self.path.setText(self.current_path)
            route = '局域网' if is_lan_address(self.settings.host) else '虚拟网络'
            self.status.setText(f'已连接 · {route} · {self.settings.host} · 手机接收目录：{receive}')
            self.choose_category('recent')

        self.start_task(work, done, f'正在连接手机 {self.settings.host}…局域网优先，虚拟网络备用。')

    def set_folder(self, result):
        self.current_path, self.entries = result
        self.category = 'files'
        self.page = 0
        self.path.setText(self.current_path)
        self.render()

    def load_folder(self, path):
        if not self.connected or self.busy:
            return

        def work(signals):
            with Phone(self.settings, self.password, self.cancel) as phone:
                return phone.list_dir(path)

        def done(result):
            self.set_folder(result)
            self.status.setText(f'手机目录 · {len(self.entries):,} 项 · 双击文件夹进入，双击文件查看详情')

        self.start_task(work, done, '正在读取手机目录…')

    def go_up(self):
        self.load_folder(posixpath.dirname(self.current_path.rstrip('/')) or '/')

    def choose_category(self, category):
        if category == 'files':
            self.load_folder(self.current_path)
            return
        self.category = category
        self.page = 0
        if self.index is not None:
            self.entries = self.index
            self.render()
            return

        def work(signals):
            last = [0.0]

            def report(message):
                if time.monotonic() - last[0] > .2:
                    last[0] = time.monotonic()
                    signals.progress.emit(message, 0, 0)
            with Phone(self.settings, self.password, self.cancel) as phone:
                return phone.scan(report)

        def done(result):
            self.index, skipped, truncated = result
            self.entries = self.index
            self.render()
            detail = f'已索引 {len(self.index):,} 个文件'
            if self.settings.key_auth:
                detail += ' · 手机媒体库索引 · 预览仅下载小缩略图 · 未收录文件可在“手机文件”中浏览'
            if skipped:
                detail += f' · {len(skipped)} 个目录无权访问'
            if truncated:
                detail += ' · 已达到 100,000 个文件上限，请进入具体目录浏览其余文件'
            self.status.setText(detail + ' · 按文件修改时间分组')
            if skipped:
                logging.info('unreadable directories: %s', '\n'.join(skipped))

        self.start_task(work, done, '正在读取手机分类索引…')

    def refresh(self):
        self.index = None
        if self.category == 'files':
            self.load_folder(self.current_path)
        else:
            self.choose_category(self.category)

    def delete_selected(self):
        entries = self.selected()
        if (not self.connected or self.busy or self.closing or not entries
                or any(e.is_dir for e in entries)):
            return
        box = QMessageBox(self)
        box.setWindowTitle('删除手机文件')
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f'确定从手机永久删除选中的 {len(entries)} 个文件吗？')
        names = '\n'.join(e.name for e in entries[:8])
        if len(entries) > 8:
            names += f'\n…另有 {len(entries) - 8} 个文件'
        box.setInformativeText(names + '\n\n删除后无法撤销。')
        box.setDetailedText('\n'.join(e.path for e in entries))
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).setText('删除')
        box.button(QMessageBox.StandardButton.Yes).setObjectName('danger')
        box.button(QMessageBox.StandardButton.No).setText('取消')
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.setEscapeButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes or not self.connected or self.busy:
            return
        removed = set()
        settings, password = replace(self.settings), self.password

        def work(signals):
            def report(path, count, total):
                removed.add(path)
                signals.progress.emit('正在删除：' + posixpath.basename(path), count, total)
            try:
                with Phone(settings, password, self.cancel) as phone:
                    return phone.delete_files(entries, report)
            except Exception:
                logging.exception('delete failed host=%s removed=%s total=%s',
                                  settings.host, len(removed), len(entries))
                raise

        def apply_deleted():
            self.entries = [e for e in self.entries if e.path not in removed]
            if self.index is not None:
                self.index = [e for e in self.index if e.path not in removed]
            self.render()

        def done(result):
            apply_deleted()
            self.status.setText(f'已从手机删除 {len(result)} 个文件。')

        def failed(error):
            apply_deleted()
            text = f'已确认删除 {len(removed)} / {len(entries)} 个文件。其余文件请刷新检查。'
            self.status.setText(('删除已取消 · ' if isinstance(error, Cancelled) else '删除未完成 · ') + text)
            if not isinstance(error, Cancelled):
                QMessageBox.warning(self, '删除未完成', text + '\n\n' + (str(error) or type(error).__name__))

        self.start_task(work, done, '正在删除选中的手机文件…', on_error=failed)

    def filtered(self):
        term = self.search.text().casefold().strip()
        now = time.time()
        entries = [e for e in self.entries if (not term or term in e.path.casefold()) and
                   (self.category == 'files' or
                    (self.category == 'recent' and now - 30 * 86400 <= e.mtime <= now) or
                    e.kind == self.category)]
        mode = self.sort.currentIndex()
        if mode == 2:
            entries.sort(key=lambda e: (not e.is_dir, e.name.casefold(), e.path))
        else:
            entries.sort(key=lambda e: (not e.is_dir, -e.mtime if mode == 0 else e.mtime, e.name.casefold()))
        return entries

    def reset_page(self, *args):
        self.page = 0
        self.render()

    def change_page(self, delta):
        if self.category != 'files':
            return
        self.page = max(0, self.page + delta)
        self.render()

    def gallery_batch(self, entries):
        batch = QWidget()
        layout = QVBoxLayout(batch)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        groups = OrderedDict()
        for entry in entries:
            date = datetime.fromtimestamp(entry.mtime).strftime('%Y年%m月%d日')
            key = '文件夹' if entry.is_dir else ('文件' if self.sort.currentIndex() == 2 else date)
            groups.setdefault(key, []).append(entry)
        for title, items in groups.items():
            count = f'{len(items)} 项' if self.category != 'files' else f'本页 {len(items)} 项'
            label = QLabel(f'{title}    ·    {count}')
            label.setObjectName('date')
            layout.addWidget(label)
            gallery = Gallery(items, self.icons)
            gallery.itemSelectionChanged.connect(self.selection_changed)
            gallery.itemDoubleClicked.connect(self.activate)
            gallery.setMinimumWidth(180)
            layout.addWidget(gallery)
            self.galleries.append(gallery)
            for i in range(gallery.count()):
                item = gallery.item(i)
                self.thumbnail_items[item.data(Qt.ItemDataRole.UserRole).path] = item
        return batch

    def update_loaded_count(self):
        hint = '向下滚动继续加载' if self.scroll_loaded < len(self.scroll_entries) else '已全部加载'
        self.page_label.setText(f'已加载 {self.scroll_loaded:,} / {len(self.scroll_entries):,} 项 · 每批 {PAGE_SIZE} 项 · {hint}')

    def maybe_load_more(self, *args):
        if (self.category == 'files' or not self.connected or self.busy or self.closing or
                self.batch_loading or self.thumb_cancel.is_set() or not self.scroll_batches or
                self.scroll_loaded >= len(self.scroll_entries)):
            return
        batch = self.scroll_batches[-1]
        viewport_bottom = self.scroll.verticalScrollBar().value() + self.scroll.viewport().height()
        if viewport_bottom < batch.y() + batch.height() / 2:
            return
        # One batch ahead, and only one thumbnail request task at a time.
        self.batch_loading = True
        entries = self.scroll_entries[self.scroll_loaded:self.scroll_loaded + PAGE_SIZE]
        self.scroll_loaded += len(entries)
        batch = self.gallery_batch(entries)
        self.scroll_batches.append(batch)
        self.gallery_layout.insertWidget(self.gallery_layout.count() - 1, batch)
        self.update_loaded_count()
        self.start_thumbnails(entries, self.thumbnail_generation)

    def resume_thumbnails(self):
        if (self.category != 'files' and self.connected and not self.busy and not self.closing and
                self.thumb_cancel.is_set() and self.scroll_loaded):
            self.thumb_cancel = CancelEvent()
            start = ((self.scroll_loaded - 1) // PAGE_SIZE) * PAGE_SIZE
            self.start_thumbnails(self.scroll_entries[start:self.scroll_loaded], self.thumbnail_generation)

    def render(self):
        self.thumb_cancel.set()
        self.thumb_cancel = CancelEvent()
        self.thumbnail_generation += 1
        generation = self.thumbnail_generation
        self.galleries = []
        self.thumbnail_items = {}
        self.scroll_batches = []
        self.scroll_loaded = 0
        self.batch_loading = False
        content = QWidget()
        content.setObjectName('galleryContent')
        content.setStyleSheet('QWidget#galleryContent { background: white; }')
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 6, 14, 14)
        layout.setSpacing(0)
        self.gallery_layout = layout
        entries = self.filtered()
        self.scroll_entries = entries if self.category != 'files' else []
        pages = max(1, math.ceil(len(entries) / PAGE_SIZE))
        self.page = min(self.page, pages - 1)
        visible = entries[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
        if self.category != 'files':
            self.page = 0
            visible = entries[:PAGE_SIZE]
        if not visible:
            empty = QLabel('连接后，在电脑上浏览你的手机文件\n\n按日期找到照片、视频和文档，再选择传输方向。' if not self.connected else '没有匹配的文件')
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setObjectName('muted')
            empty.setMinimumHeight(280)
            layout.addWidget(empty)
        if visible:
            batch = self.gallery_batch(visible)
            layout.addWidget(batch)
            if self.category != 'files':
                self.scroll_batches.append(batch)
                self.scroll_loaded = len(visible)
        layout.addStretch()
        old = self.scroll.takeWidget()
        if old:
            old.deleteLater()
        self.scroll.setWidget(content)
        self.scroll.verticalScrollBar().setValue(0)
        self.page_label.setText(f'第 {self.page + 1} / {pages} 页 · 每页 {PAGE_SIZE} 项 · 共 {len(entries):,} 项')
        self.previous.setVisible(self.category == 'files')
        self.next.setVisible(self.category == 'files')
        self.all_button.setText('全选已加载' if self.category != 'files' else '全选本页')
        if self.category != 'files':
            self.update_loaded_count()
        titles = {'files': '手机文件', 'recent': '最近 30 天', 'image': '图片', 'video': '视频',
                  'audio': '音乐', 'document': '文档 / 压缩包'}
        self.title.setText(titles[self.category])
        for key, button in self.nav.items():
            button.setChecked(key == self.category)
        self.selection_changed()
        self.update_controls()
        if self.connected and not self.busy:
            self.start_thumbnails(visible, generation)

    def start_thumbnails(self, entries, generation):
        cancel = self.thumb_cancel
        if self.category != 'files':
            self.batch_loading = True

        def finish_batch():
            if generation == self.thumbnail_generation and cancel is self.thumb_cancel:
                self.batch_loading = False
                QTimer.singleShot(0, self.maybe_load_more)

        entries = [e for e in entries if e.kind in {'image', 'video'}]
        if not entries:
            # Let Qt lay out the new batch before accepting more scroll signals.
            QTimer.singleShot(0, finish_batch)
            return
        settings, password = replace(self.settings), self.password

        def work(signals):
            with Phone(settings, password, cancel) as phone:
                for e in entries:
                    phone.check_cancel()
                    try:
                        thumb = phone.thumbnail(e)
                        if thumb:
                            signals.thumbnail.emit(e.path, thumb)
                    except Cancelled:
                        raise
                    except (OSError, ValueError):
                        continue

        def apply(path, thumb):
            if generation != self.thumbnail_generation or cancel.is_set():
                return
            item = self.thumbnail_items.get(path)
            if item is not None:
                item.setIcon(QIcon(thumb))

        task = Task(work)
        self.tasks.add(task)
        def finished(result):
            self.tasks.discard(task)
            finish_batch()
        task.signals.thumbnail.connect(apply)
        task.signals.done.connect(finished)
        task.signals.error.connect(finished)
        self.pool.start(task)

    def activate(self, item):
        entry = item.data(Qt.ItemDataRole.UserRole)
        if entry.is_dir:
            self.load_folder(entry.path)
        elif entry.kind == 'image':
            self.preview_image(entry)
        else:
            box = QMessageBox(self)
            box.setWindowTitle(entry.name)
            box.setText(entry.path)
            box.setInformativeText(f'{human_size(entry.size)}\n修改时间：{datetime.fromtimestamp(entry.mtime):%Y-%m-%d %H:%M:%S}')
            box.setStandardButtons(QMessageBox.StandardButton.Ok)
            download = box.addButton('传输到电脑', QMessageBox.ButtonRole.ActionRole)
            box.exec()
            if box.clickedButton() == download:
                self.transfer('download', [entry])

    def preview_image(self, entry):
        if self.busy or not self.connected:
            return
        if self.image_preview is not None:
            self.image_preview.close()
        preview = ImagePreview(entry, self)
        self.image_preview = preview
        settings, password = replace(self.settings), self.password

        def work(signals):
            cancel = self.cancel
            # Decode off the UI thread; remove the full-size temporary copy even on cancellation.
            with tempfile.TemporaryDirectory(prefix='lc-image-') as temp:
                with Phone(settings, password, cancel) as phone:
                    phone.download([entry], Path(temp),
                                   lambda name, done, total, count: signals.progress.emit(name, done, total))
                    phone.check_cancel()
                source = next(Path(temp).iterdir())
                reader = QImageReader(str(source))
                reader.setAutoTransform(True)
                image = reader.read()
                error = reader.errorString()
                del reader  # Release the Windows file handle before removing the temporary directory.
                if image.isNull():
                    raise ValueError('图片格式不受支持或文件损坏：' + error)
                if cancel.is_set():
                    raise Cancelled('已取消原图预览。')
                return image, source.stat().st_size

        def done(result):
            if self.image_preview is preview:
                preview.loaded(*result)
                self.status.setText('原图预览 · ' + entry.name)
            else:
                self.status.setText('已关闭原图预览。')

        def failed(error):
            if self.image_preview is preview:
                preview.failed(error)
                self.status.setText('原图预览未完成。')
            else:
                self.status.setText('已关闭原图预览。')

        task = self.start_task(work, done, '正在加载原图…', on_error=failed)
        cancel = self.cancel
        task.signals.progress.connect(preview.show_progress)

        def closed(result):
            cancel.set()
            if self.image_preview is preview:
                self.image_preview = None
            preview.deleteLater()

        preview.finished.connect(closed)
        preview.show()

    def download_selected(self):
        entries = self.selected()
        if entries:
            self.transfer('download', entries)

    def dragEnterEvent(self, event):
        event.ignore()
        if not self.connected or self.busy or self.closing:
            return
        urls = event.mimeData().urls()
        if (urls and event.possibleActions() & Qt.DropAction.CopyAction and
                all(url.isLocalFile() and Path(url.toLocalFile()).is_absolute() for url in urls)):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        self.dragEnterEvent(event)
        if event.isAccepted():
            paths = list(dict.fromkeys(url.toLocalFile() for url in event.mimeData().urls()))
            self.transfer('upload', paths, choose_destination=False)

    def upload_folder(self):
        folder = QFileDialog.getExistingDirectory(self, '选择发送至手机的文件夹')
        if folder:
            self.transfer('upload', [folder])

    def receive_shared(self, request):
        if not self.connected or self.busy or self.closing:
            return
        self.share_retry_at = time.monotonic() + 10
        previous = next((job for job in self.history.snapshot()['jobs']
                         if job.get('share_id') == request['id']
                         and job.get('fingerprint') == self.settings.fingerprint), None)
        if previous:
            def work(signals):
                with Phone(self.settings, self.password, self.cancel) as phone:
                    self.history.mirror(phone)
                    phone.complete_share(request)
            def failed(error):
                self.share_retry_at = time.monotonic() + 30
                logging.warning('share acknowledgement deferred: %s', error)
                self.status.setText('分享接收状态待同步，稍后自动重试。')
            self.start_task(work, lambda _: None, '正在同步手机分享状态…', on_error=failed)
        else:
            self.transfer('download', request['entries'], share_request=request)

    def transfer(self, direction, entries, *, choose_destination=True, share_request=None):
        target = self.current_path
        if direction == 'upload':
            target = self.settings.receive_dir
            if choose_destination and not self.settings.key_auth:
                from PySide6.QtWidgets import QInputDialog
                target, ok = QInputDialog.getText(self, '发送至手机', '手机接收目录：', text=target)
                if not ok or not target.strip():
                    return
                target = target.strip()
        receiver = self.settings.download_dir
        names = [e.name if isinstance(e, Entry) else Path(e).name for e in entries]
        job_id = self.history.start(direction, names, target if direction == 'upload' else receiver,
                                    self.settings.host, self.settings.fingerprint,
                                    share_id=share_request['id'] if share_request else '')
        self.show_transfers()

        def work(signals):
            last = [0.0]
            start = time.monotonic()
            file_started = [start]
            destination = target if direction == 'upload' else receiver

            def report(name, done, total, count):
                now = time.monotonic()
                if done == 0:
                    file_started[0] = now
                if now - last[0] > .15 or done == total:
                    last[0] = now
                    verb = '下载' if direction == 'download' else '上传'
                    speed = done / max(.001, now - file_started[0])
                    signals.progress.emit(f'{verb} {name} · {human_size(done)} / {human_size(total)} · {human_size(speed)}/s · 已完成 {count} 个文件', done, total)
            phone = None
            last_mirror = [0.0]
            last_event = {}
            logging.info('transfer start job=%s direction=%s host=%s target=%s files=%s',
                         job_id, direction, self.settings.host, destination, names)

            def event(data):
                nonlocal destination
                last_event.update(data)
                self.history.event(job_id, data)
                if direction == 'download' and data['state'] == 'done':
                    destination = data['target']
                if phone is not None and time.monotonic() - last_mirror[0] > 2:
                    last_mirror[0] = time.monotonic()
                    self.history.mirror(phone)

            try:
                with Phone(self.settings, self.password, self.cancel, event) as phone:
                    self.history.mirror(phone)
                    try:
                        if direction == 'download':
                            if share_request or (len(entries) == 1 and not entries[0].is_dir):
                                count, archive = phone.download(entries, Path(receiver), report), ''
                            else:
                                count, archive = phone.download_zip(entries, Path(receiver), report)
                                destination = archive
                        else:
                            count, archive = phone.upload(entries, target, report), ''
                        self.history.finish(job_id, 'done', archive=archive)
                    except Exception as ex:
                        self.history.finish(job_id, 'cancelled' if isinstance(ex, Cancelled) else 'error', str(ex) or type(ex).__name__)
                        raise
                    finally:
                        self.history.mirror(phone)
                        if share_request:
                            try:
                                phone.complete_share(share_request)
                            except (OSError, EOFError, ValueError, paramiko.SSHException) as error:
                                logging.warning('share acknowledgement deferred: %s', error)
            except Exception as ex:
                state = 'cancelled' if isinstance(ex, Cancelled) else 'error'
                self.history.finish(job_id, state, str(ex) or type(ex).__name__)
                logging.exception('transfer failed job=%s direction=%s host=%s file=%s confirmed=%s total=%s error_type=%s',
                                  job_id, direction, self.settings.host, last_event.get('name', ''),
                                  last_event.get('done', 0), last_event.get('size', 0), type(ex).__name__)
                raise
            logging.info('transfer complete job=%s direction=%s files=%s elapsed=%.3f target=%s',
                         job_id, direction, count, time.monotonic() - start, destination)
            return count, time.monotonic() - start, destination

        def done(result):
            count, seconds, destination = result
            self.index = None
            self.status.setText(f'传输完成 · {count} 个文件 · {seconds:.1f} 秒 · 接收位置：' + destination + (' · ' + self.history.warning if self.history.warning else ''))
            if direction == 'upload' and self.category == 'files':
                self.load_folder(self.current_path)

        self.start_task(work, done, '正在准备传输…同名文件会自动另存，不覆盖原文件。')

    def closeEvent(self, event):
        if self.busy:
            QMessageBox.information(self, '操作仍在进行', '请先点击“取消操作”，等待结束后再关闭。')
            event.ignore()
            return
        self.thumb_cancel.set()
        self.closing = True
        self.discovery_timer.stop()
        if self.control:
            self.control.__exit__()
            self.control = None
        self.discovery_pool.waitForDone(6000)
        self.pool.clear()
        self.hide()
        self.pool.waitForDone(30000)
        event.accept()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--smoke-test', action='store_true')
    parser.add_argument('--screenshot')
    args = parser.parse_args()
    logging.basicConfig(handlers=[RotatingFileHandler(data_dir() / 'client.log',
                        maxBytes=2 * 1024 * 1024, backupCount=2, encoding='utf-8')],
                        level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger('paramiko').setLevel(logging.WARNING)
    app = QApplication(sys.argv[:1])
    if sys.platform == 'win32':
        # The offscreen plugin cannot discover Windows' font fallback by itself.
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    app.setApplicationName('LC 互传')
    app.setStyleSheet(STYLE)
    window = Window(auto_discover=not args.smoke_test)
    window.show()
    window.raise_()
    window.activateWindow()
    if args.smoke_test:
        def capture():
            if args.screenshot:
                window.grab().save(args.screenshot)
            if getattr(sys, 'frozen', False):
                faulthandler.cancel_dump_traceback_later()
            app.quit()
        QTimer.singleShot(800, capture)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
