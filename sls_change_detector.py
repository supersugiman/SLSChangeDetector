# sls_change_detector.py
# Plugin QGIS untuk deteksi perubahan geometri, atribut, dan LUAS antara dua file SLS
# + Deteksi penambahan/penghapusan fitur
# + Hitung perubahan luas → deteksi "Perubahan Batas SLS"
# + Export ke CSV

from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QMessageBox, QTableWidget, QTableWidgetItem,
    QGroupBox, QGridLayout, QDialogButtonBox, QTextEdit, QTabWidget, QToolBar,
    QWidget
)
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt, QDateTime
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsField, QgsFields,
    QgsVectorFileWriter, QgsCoordinateReferenceSystem, QgsWkbTypes
)
from qgis.utils import iface
import os
import time
import csv


class SLSChangeDetectorDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Deteksi Perubahan SLS (BPS1471)")
        self.resize(1000, 750)

        self.changes = []  # Simpan semua perubahan

        layout = QVBoxLayout(self)

        # Group Box: Input Files
        input_group = QGroupBox("Input Files")
        input_layout = QGridLayout()
        input_group.setLayout(input_layout)

        # Old File
        old_label = QLabel("File SLS Lama (semester 2 2024):")
        self.old_line = QLineEdit()
        self.old_btn = QPushButton("...")
        self.old_btn.clicked.connect(self.select_old_file)
        input_layout.addWidget(old_label, 0, 0)
        input_layout.addWidget(self.old_line, 0, 1)
        input_layout.addWidget(self.old_btn, 0, 2)

        # New File
        new_label = QLabel("File SLS Baru (semester 1 2025):")
        self.new_line = QLineEdit()
        self.new_btn = QPushButton("...")
        self.new_btn.clicked.connect(self.select_new_file)
        input_layout.addWidget(new_label, 1, 0)
        input_layout.addWidget(self.new_line, 1, 1)
        input_layout.addWidget(self.new_btn, 1, 2)

        layout.addWidget(input_group)

        # Button: Run
        self.run_btn = QPushButton("Deteksi Perubahan")
        self.run_btn.clicked.connect(self.run_detection)
        layout.addWidget(self.run_btn)

        # Tab Widget: Results
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Tab 1: Summary
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.tabs.addTab(self.summary_text, "Ringkasan")

        # Tab 2: Changed Features
        table_layout = QVBoxLayout()
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "idsls", "Status", "Perubahan Batas SLS", "Luas Lama", "Luas Baru", "Selisih Luas", "subsls Berubah"
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        table_layout.addWidget(self.table)

        # Tombol Export
        self.export_btn = QPushButton("Export Hasil ke CSV")
        self.export_btn.clicked.connect(self.export_to_csv)
        self.export_btn.setEnabled(False)
        table_layout.addWidget(self.export_btn)

        table_widget = QWidget()
        table_widget.setLayout(table_layout)
        self.tabs.addTab(table_widget, "Daftar Perubahan")

    def select_old_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Pilih File SLS Lama", "", "GeoPackage (*.gpkg)")
        if file_path:
            self.old_line.setText(file_path)

    def select_new_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Pilih File SLS Baru", "", "GeoPackage (*.gpkg)")
        if file_path:
            self.new_line.setText(file_path)

    def run_detection(self):
        old_path = self.old_line.text().strip()
        new_path = self.new_line.text().strip()

        if not old_path or not new_path:
            QMessageBox.warning(self, "Error", "Harap pilih kedua file!")
            return

        if not os.path.exists(old_path):
            QMessageBox.warning(self, "Error", f"File lama tidak ditemukan: {old_path}")
            return
        if not os.path.exists(new_path):
            QMessageBox.warning(self, "Error", f"File baru tidak ditemukan: {new_path}")
            return

        # Load layers
        old_layer = QgsVectorLayer(old_path, "sls_lama", "ogr")
        new_layer = QgsVectorLayer(new_path, "sls_baru", "ogr")

        if not old_layer.isValid() or not new_layer.isValid():
            QMessageBox.critical(self, "Error", "Gagal membuka salah satu file GeoPackage!")
            return

        # Cek field 'idsls' dan 'LUAS'
        required_fields = ["idsls", "luas"]
        for field in required_fields:
            if field not in [f.name() for f in old_layer.fields()] or field not in [f.name() for f in new_layer.fields()]:
                QMessageBox.critical(self, "Error", f"Field '{field}' tidak ditemukan di salah satu layer!")
                return

        # Siapkan struktur data
        old_features = {}
        for feat in old_layer.getFeatures():
            idsls = feat["idsls"]
            old_features[idsls] = {
                "geom_wkt": feat.geometry().asWkt() if feat.geometry() else None,
                "LUAS": float(feat["LUAS"]) if feat["LUAS"] is not None else 0.0,
                "subsls": feat["subsls"] if "subsls" in feat.fields().names() else None
            }

        new_features = {}
        for feat in new_layer.getFeatures():
            idsls = feat["idsls"]
            new_features[idsls] = {
                "geom_wkt": feat.geometry().asWkt() if feat.geometry() else None,
                "LUAS": float(feat["LUAS"]) if feat["LUAS"] is not None else 0.0,
                "subsls": feat["subsls"] if "subsls" in feat.fields().names() else None
            }

        self.changes = []
        total_old = len(old_features)
        total_new = len(new_features)
        batas_berubah = 0
        subsls_berubah = 0
        ditambahkan = 0
        dihapus = 0

        # 1. Cek fitur yang ada di kedua file
        for idsls in set(old_features.keys()) & set(new_features.keys()):
            old_feat = old_features[idsls]
            new_feat = new_features[idsls]

            # Cek perubahan geometri (WKT)
            geom_changed = old_feat["geom_wkt"] != new_feat["geom_wkt"]
            # Cek perubahan LUAS (toleransi kecil untuk floating point)
            luas_changed = abs(old_feat["LUAS"] - new_feat["LUAS"]) > 0.001
            # Cek perubahan subsls
            subsls_changed = old_feat["subsls"] != new_feat["subsls"]

            # Jika geometri atau LUAS berubah → anggap "Perubahan Batas SLS"
            batas_sls_berubah = geom_changed or luas_changed

            if batas_sls_berubah or subsls_changed:
                status = "DIUBAH"
                if batas_sls_berubah:
                    batas_berubah += 1
                if subsls_changed:
                    subsls_berubah += 1

                self.changes.append({
                    "idsls": idsls,
                    "status": status,
                    "batas_berubah": batas_sls_berubah,
                    "luas_lama": old_feat["LUAS"],
                    "luas_baru": new_feat["LUAS"],
                    "selisih_luas": new_feat["LUAS"] - old_feat["LUAS"],
                    "subsls_lama": old_feat["subsls"],
                    "subsls_baru": new_feat["subsls"],
                    "subsls_changed": subsls_changed
                })

        # 2. Cek fitur yang DITAMBAHKAN (ada di baru, tidak ada di lama)
        for idsls in set(new_features.keys()) - set(old_features.keys()):
            new_feat = new_features[idsls]
            ditambahkan += 1
            self.changes.append({
                "idsls": idsls,
                "status": "DITAMBAHKAN",
                "batas_berubah": False,  # Tidak ada perbandingan
                "luas_lama": 0.0,
                "luas_baru": new_feat["LUAS"],
                "selisih_luas": new_feat["LUAS"],
                "subsls_lama": None,
                "subsls_baru": new_feat["subsls"],
                "subsls_changed": False
            })

        # 3. Cek fitur yang DIHAPUS (ada di lama, tidak ada di baru)
        for idsls in set(old_features.keys()) - set(new_features.keys()):
            old_feat = old_features[idsls]
            dihapus += 1
            self.changes.append({
                "idsls": idsls,
                "status": "DIHAPUS",
                "batas_berubah": False,  # Tidak ada perbandingan
                "luas_lama": old_feat["LUAS"],
                "luas_baru": 0.0,
                "selisih_luas": -old_feat["LUAS"],
                "subsls_lama": old_feat["subsls"],
                "subsls_baru": None,
                "subsls_changed": False
            })

        # Tampilkan ringkasan
        summary = f"""
📊 RINGKASAN PERUBAHAN:

Total fitur di file lama: {total_old}
Total fitur di file baru: {total_new}

Perubahan terdeteksi:
- Fitur yang mengalami perubahan batas SLS: {batas_berubah}
- Fitur yang mengalami perubahan atribut 'subsls': {subsls_berubah}
- Fitur yang ditambahkan: {ditambahkan}
- Fitur yang dihapus: {dihapus}
- Total fitur yang berubah: {len(self.changes)}

✅ Siap menampilkan daftar perubahan...
        """
        self.summary_text.setText(summary)

        # Tampilkan tabel
        self.table.setRowCount(len(self.changes))
        for row, change in enumerate(self.changes):
            self.table.setItem(row, 0, QTableWidgetItem(str(change["idsls"])))
            self.table.setItem(row, 1, QTableWidgetItem(change["status"]))
            self.table.setItem(row, 2, QTableWidgetItem("✅ Ya" if change["batas_berubah"] else "❌ Tidak"))
            self.table.setItem(row, 3, QTableWidgetItem(f"{change['luas_lama']:.2f}"))
            self.table.setItem(row, 4, QTableWidgetItem(f"{change['luas_baru']:.2f}"))
            self.table.setItem(row, 5, QTableWidgetItem(f"{change['selisih_luas']:+.2f}"))
            self.table.setItem(row, 6, QTableWidgetItem("✅ Ya" if change["subsls_changed"] else "❌ Tidak"))

        # Aktifkan tombol export
        self.export_btn.setEnabled(len(self.changes) > 0)

        # Buat layer visualisasi — HANYA untuk fitur yang mengalami perubahan BATAS (geometri)
        self.create_boundary_change_layer(new_layer, old_layer)

        QMessageBox.information(self, "Selesai", f"Deteksi selesai!\n{len(self.changes)} fitur berubah.")

    def create_boundary_change_layer(self, new_layer, old_layer):
        """Buat layer khusus untuk fitur yang mengalami perubahan BATAS (geometri)"""
        fields = new_layer.fields()
        crs = new_layer.crs()
        uri = f"Polygon?crs={crs.authid()}"
        boundary_change_layer = QgsVectorLayer(uri, "SLS_Batas_Berubah", "memory")
        boundary_change_layer.dataProvider().addAttributes(fields)
        boundary_change_layer.updateFields()

        # Tambahkan fitur yang geometrinya berubah (hanya yang ada di kedua file)
        features_to_add = []
        for feat in new_layer.getFeatures():
            idsls = feat["idsls"]
            # Cek apakah ini fitur yang berubah batas
            for change in self.changes:
                if change["idsls"] == idsls and change["batas_berubah"] and change["status"] == "DIUBAH":
                    new_feat = QgsFeature()
                    new_feat.setGeometry(feat.geometry())
                    new_feat.setAttributes(feat.attributes())
                    features_to_add.append(new_feat)
                    break

        if features_to_add:
            boundary_change_layer.dataProvider().addFeatures(features_to_add)
            boundary_change_layer.updateExtents()
            QgsProject.instance().addMapLayer(boundary_change_layer)
            self.summary_text.append(f"\n✅ Layer 'SLS_Batas_Berubah' telah ditambahkan ke project (hanya polygon yang berubah batas).")
        else:
            self.summary_text.append(f"\nℹ️ Tidak ada polygon yang mengalami perubahan batas.")

    def export_to_csv(self):
        """Export hasil perubahan ke file CSV"""
        if not self.changes:
            QMessageBox.warning(self, "Peringatan", "Tidak ada data untuk di-export.")
            return

        default_name = "SLS_Perubahan_" + time.strftime("%Y%m%d_%H%M%S") + ".csv"
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Simpan Hasil ke CSV", default_name, "CSV Files (*.csv)"
        )

        if not save_path:
            return

        if not save_path.lower().endswith('.csv'):
            save_path += '.csv'

        try:
            with open(save_path, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = [
                    "idsls", "Status", "Perubahan_Batas_SLS", "Luas_Lama", "Luas_Baru", "Selisih_Luas",
                    "subsls_Lama", "subsls_Baru", "Perubahan_subsls", "Catatan"
                ]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()

                for change in self.changes:
                    catatan = []
                    if change["status"] == "DIUBAH":
                        if change["batas_berubah"]:
                            catatan.append("Batas berubah")
                        if change["subsls_changed"]:
                            catatan.append("subsls berubah")
                    else:
                        catatan.append(change["status"])

                    writer.writerow({
                        "idsls": change["idsls"],
                        "Status": change["status"],
                        "Perubahan_Batas_SLS": "Ya" if change["batas_berubah"] else "Tidak",
                        "Luas_Lama": f"{change['luas_lama']:.4f}",
                        "Luas_Baru": f"{change['luas_baru']:.4f}",
                        "Selisih_Luas": f"{change['selisih_luas']:+.4f}",
                        "subsls_Lama": change["subsls_lama"] if change["subsls_lama"] is not None else "NULL",
                        "subsls_Baru": change["subsls_baru"] if change["subsls_baru"] is not None else "NULL",
                        "Perubahan_subsls": "Ya" if change["subsls_changed"] else "Tidak",
                        "Catatan": "; ".join(catatan)
                    })

            QMessageBox.information(self, "Sukses", f"Hasil berhasil di-export ke:\n{save_path}")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Gagal export ke CSV:\n{str(e)}")


class SLSChangeDetectorPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, 'icon.png')
        self.action = QAction(
            QIcon(icon_path) if os.path.exists(icon_path) else QIcon(),
            "SLS Change Detector (BPS1471)",  # Nama menu
            self.iface.mainWindow()
        )
        self.action.triggered.connect(self.run)
        self.iface.addPluginToVectorMenu("SLS Tools", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        self.iface.removePluginVectorMenu("SLS Tools", self.action)
        self.iface.removeToolBarIcon(self.action)

    def run(self):
        dialog = SLSChangeDetectorDialog(self.iface.mainWindow())
        dialog.exec_()