"""Selection clears across independent date galleries when blank space is clicked."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from desktop import Entry, Window


class GallerySelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,LOCALAPPDATA=self.temp.name)
        self.env.start()
        self.window=Window(auto_discover=False)
        self.window.start_thumbnails=lambda *args:None
        self.window.connected=True
        self.window.category='recent'
        now=time.time()-60
        self.window.entries=[Entry(f'/DCIM/day{day}-photo{photo}.jpg',f'day{day}-photo{photo}.jpg',1000,now-day*86400)
                             for day in range(2) for photo in range(2)]
        self.window.resize(1280,1100)
        self.window.show()
        self.window.render()
        QTest.qWait(60)
        self.assertEqual(len(self.window.galleries),2)

    def tearDown(self):
        self.window.close()
        self.app.processEvents()
        self.env.stop()
        self.temp.cleanup()

    def click_item(self,gallery,index,modifiers=Qt.KeyboardModifier.NoModifier):
        QTest.mouseClick(gallery.viewport(),Qt.MouseButton.LeftButton,modifiers,
                         gallery.visualItemRect(gallery.item(index)).center())

    def blank(self,gallery):
        point=QPoint(gallery.viewport().width()-10,20)
        self.assertIsNone(gallery.itemAt(point))
        return point

    def assert_empty(self):
        self.assertFalse(self.window.selected())
        self.assertIn('已选择 0 项',self.window.selection.text())
        self.assertFalse(self.window.download_button.isEnabled())
        self.assertFalse(self.window.delete_button.isEnabled())

    def test_other_date_blank_header_and_outer_blank_clear_all_selections(self):
        first,second=self.window.galleries
        for selected,blank in [(first,second),(second,first)]:
            with self.subTest(selected_date=selected.item(0).text()):
                self.click_item(selected,0)
                self.assertEqual(len(self.window.selected()),1)
                self.assertTrue(self.window.download_button.isEnabled())
                QTest.mouseClick(blank.viewport(),Qt.MouseButton.LeftButton,pos=self.blank(blank))
                self.assert_empty()
        labels=self.window.scroll.widget().findChildren(QLabel,'date')
        self.assertEqual(len(labels),2)
        for target,point in [(labels[1],QPoint(15,15)),
                             (self.window.scroll.widget(),QPoint(2,2)),
                             (self.window.scroll.viewport(),QPoint(2,2))]:
            self.click_item(first,0)
            QTest.mouseClick(target,Qt.MouseButton.LeftButton,pos=point)
            self.assert_empty()

    def test_ctrl_shift_multiselect_and_right_click_keep_existing_behavior(self):
        first,second=self.window.galleries
        self.click_item(first,0)
        self.click_item(first,1,Qt.KeyboardModifier.ControlModifier)
        self.click_item(second,0,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(self.window.selected()),3)
        self.click_item(second,1,Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(len(self.window.selected()),4)
        expected=list(self.window.selected())
        label=self.window.scroll.widget().findChildren(QLabel,'date')[1]
        for modifier in [Qt.KeyboardModifier.ControlModifier,Qt.KeyboardModifier.ShiftModifier]:
            QTest.mouseClick(label,Qt.MouseButton.LeftButton,modifier,QPoint(15,15))
            self.assertEqual(self.window.selected(),expected)
        QTest.mouseClick(label,Qt.MouseButton.RightButton,pos=QPoint(15,15))
        self.assertEqual(self.window.selected(),expected)
        QTest.mouseClick(second.viewport(),Qt.MouseButton.LeftButton,pos=self.blank(second))
        self.assert_empty()


if __name__=='__main__':
    unittest.main()
