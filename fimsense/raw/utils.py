try:
    from qgis.PyQt.QtWidgets import QMessageBox, QProgressBar, QInputDialog, QLineEdit
    _QGIS_AVAILABLE = True
except ImportError:
    _QGIS_AVAILABLE = False

import os
from subprocess import Popen, PIPE
import sys,os
from osgeo import gdal

def readImageGDAL(filename):
    dataset = gdal.Open(filename, gdal.GA_ReadOnly)
    if not dataset:
        raise FileNotFoundError(f"Failed to open {filename}")
    band = dataset.GetRasterBand(1)
    image = band.ReadAsArray()
    return image, dataset

def writeImageGDAL(filename, data, reference):
    driver = gdal.GetDriverByName("GTiff")
    out_dataset = driver.Create(filename, data.shape[1], data.shape[0], 1, gdal.GDT_Byte)
    if not out_dataset:
        raise RuntimeError(f"Failed to create {filename}")
    out_dataset.SetGeoTransform(reference.GetGeoTransform())
    out_dataset.SetProjection(reference.GetProjectionRef())
    out_band = out_dataset.GetRasterBand(1)
    out_band.WriteArray(data)
    out_band.FlushCache()

def create_message(title, message):
    if not _QGIS_AVAILABLE:
        print(f"[{title}] {message}")
        return None
    msg = QMessageBox()
    msg.setWindowTitle(f'{title}')
    msg.setText(f'{message}')
    msg.addButton(QMessageBox.Yes)
    msg.addButton(QMessageBox.No)
    ret = msg.exec_()
    return ret
