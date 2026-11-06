"""In-client original image display; network work stays in the existing task queue."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (QDialog, QGraphicsScene, QGraphicsView, QHBoxLayout,
                              QLabel, QProgressBar, QPushButton, QVBoxLayout)

from transfer_core import human_size


class ImageView(QGraphicsView):
    scale_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor('#edf2f2'))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.fitted = True

    def set_image(self, image):
        self.scene().clear()
        item = self.scene().addPixmap(QPixmap.fromImage(image))
        self.scene().setSceneRect(item.boundingRect())
        self.fit_image()

    def set_scale(self, scale):
        self.resetTransform()
        self.scale(scale, scale)
        self.scale_changed.emit(scale * self.devicePixelRatioF())

    def fit_image(self):
        rect = self.sceneRect()
        if rect.isEmpty():
            return
        self.fitted = True
        scale = min((self.viewport().width() - 4) / rect.width(),
                    (self.viewport().height() - 4) / rect.height(),
                    1 / self.devicePixelRatioF())
        self.set_scale(max(.001, scale))
        self.centerOn(rect.center())

    def actual_size(self):
        self.fitted = False
        self.set_scale(1 / self.devicePixelRatioF())

    def zoom(self, factor):
        if self.sceneRect().isEmpty():
            return
        self.fitted = False
        current = self.transform().m11()
        target = min(16, max(.01, current * factor * self.devicePixelRatioF()))
        self.scale(target / self.devicePixelRatioF() / current,
                   target / self.devicePixelRatioF() / current)
        self.scale_changed.emit(target)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fitted:
            self.fit_image()

    def wheelEvent(self, event):
        if not self.sceneRect().isEmpty() and event.angleDelta().y():
            self.zoom(1.25 if event.angleDelta().y() > 0 else .8)
            event.accept()
        else:
            super().wheelEvent(event)


class ImagePreview(QDialog):
    def __init__(self, entry, parent):
        super().__init__(parent)
        self.setWindowTitle('原图预览 · ' + entry.name)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(1100, 760)
        self.setMinimumSize(640, 420)
        layout = QVBoxLayout(self)
        self.info = QLabel('正在从手机加载原图…')
        self.info.setTextFormat(Qt.TextFormat.PlainText)
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        toolbar = QHBoxLayout()
        self.view = ImageView(self)
        self.zoom_buttons = []
        for text, action in [('适应窗口', self.view.fit_image), ('100%', self.view.actual_size),
                             ('−', lambda: self.view.zoom(.8)), ('＋', lambda: self.view.zoom(1.25))]:
            button = QPushButton(text)
            button.setEnabled(False)
            button.clicked.connect(action)
            toolbar.addWidget(button)
            self.zoom_buttons.append(button)
        self.percent = QLabel('')
        self.view.scale_changed.connect(lambda scale: self.percent.setText(f'{scale:.0%}'))
        toolbar.addWidget(self.percent)
        toolbar.addStretch()
        close = QPushButton('关闭')
        close.clicked.connect(self.reject)
        toolbar.addWidget(close)
        layout.addLayout(toolbar)
        layout.addWidget(self.view, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)

    def show_progress(self, message, done, total):
        self.info.setText(f'正在加载原图 · {human_size(done)} / {human_size(total)}')
        if total > 0:
            self.progress.setRange(0, 1000)
            self.progress.setValue(min(1000, int(done / total * 1000)))

    def loaded(self, image, size):
        self.progress.hide()
        self.view.set_image(image)
        for button in self.zoom_buttons:
            button.setEnabled(True)
        self.info.setText(f'{image.width():,} × {image.height():,} · {human_size(size)} · 原图'
                          ' · 滚轮缩放，拖动查看')

    def failed(self, error):
        self.progress.hide()
        self.info.setText('原图加载失败：' + str(error))
