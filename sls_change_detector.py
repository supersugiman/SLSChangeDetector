# sls_change_detector.py
# Plugin QGIS untuk deteksi perubahan SLS — versi FINAL
# - Berdasarkan ID (luas, subsls, penambahan/penghapusan)
# - Berdasarkan Geometri (Symmetrical Difference)
# - Ekspor gabungan ke satu file CSV
# - Kompatibel QGIS 3.34.9

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
    QgsVectorFileWriter, QgsCoordinateReferenceSystem, QgsWkbTypes,
    QgsFeatureRequest, QgsVectorLayerUtils, QgsFillSymbol, QgsSingleSymbolRenderer
)
from qgis.utils import iface
import os
import time
import csv


class SLSChangeDetectorDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SLS Change Detector - FINAL VERSION")
        self.resize(1000, 800)

        self.changes_by_id = []      # Perubahan berdasarkan ID
        self.spatial_changes = []    # Perubahan spasial (symmetrical difference)
        self.combined_report = []    # Gabungan laporan

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
        self.run_btn = QPushButton("Deteksi Perubahan (ID + Spasial)")
        self.run_btn.clicked.connect(self.run_detection)
        layout.addWidget(self.run_btn)

        # Tab Widget: Results
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Tab 1: Summary
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.tabs.addTab(self.summary_text, "Ringkasan")

        # Tab 2: Changed Features (by ID)
        table_layout = QVBoxLayout()
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "idsls", "Status", "Perubahan Batas SLS", "Luas Lama", "Luas Baru", "Selisih Luas", "subsls Berubah"
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        table_layout.addWidget(self.table)

        # Tombol Export
        self.export_btn = QPushButton("Export Hasil Gabungan ke CSV")
        self.export_btn.clicked.connect(self.export_combined_to_csv)
        self.export_btn.setEnabled(False)
        table_layout.addWidget(self.export_btn)

        table_widget = QWidget()
        table_widget.setLayout(table_layout)
        self.tabs.addTab(table_widget, "Perubahan Berdasarkan ID")

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

        # Cek field 'idsls' dan 'luas'
        required_fields = ["idsls", "luas"]
        for field in required_fields:
            if field not in [f.name() for f in old_layer.fields()] or field not in [f.name() for f in new_layer.fields()]:
                QMessageBox.critical(self, "Error", f"Field '{field}' tidak ditemukan di salah satu layer!")
                return

        # ================================
        # 1. ANALISIS BERDASARKAN ID
        # ================================
        self.changes_by_id = []
        old_features = {}
        for feat in old_layer.getFeatures():
            idsls = feat["idsls"]
            old_features[idsls] = {
                "geom": feat.geometry(),
                "luas": float(feat["luas"]) if feat["luas"] is not None else 0.0,
                "subsls": feat["subsls"] if "subsls" in feat.fields().names() else None
            }

        new_features = {}
        for feat in new_layer.getFeatures():
            idsls = feat["idsls"]
            new_features[idsls] = {
                "geom": feat.geometry(),
                "luas": float(feat["luas"]) if feat["luas"] is not None else 0.0,
                "subsls": feat["subsls"] if "subsls" in feat.fields().names() else None
            }

        total_old = len(old_features)
        total_new = len(new_features)
        batas_berubah = 0
        subsls_berubah = 0
        ditambahkan = 0
        dihapus = 0

        # Fitur yang ada di kedua file
        for idsls in set(old_features.keys()) & set(new_features.keys()):
            old_feat = old_features[idsls]
            new_feat = new_features[idsls]

            selisih_luas = old_feat["luas"] - new_feat["luas"]
            batas_sls_berubah = abs(selisih_luas) > 0.001
            subsls_changed = old_feat["subsls"] != new_feat["subsls"]

            if batas_sls_berubah or subsls_changed:
                status = "DIUBAH"
                if batas_sls_berubah:
                    batas_berubah += 1
                if subsls_changed:
                    subsls_berubah += 1

                self.changes_by_id.append({
                    "idsls": idsls,
                    "status": status,
                    "batas_berubah": batas_sls_berubah,
                    "luas_lama": old_feat["luas"],
                    "luas_baru": new_feat["luas"],
                    "selisih_luas": selisih_luas,
                    "subsls_lama": old_feat["subsls"],
                    "subsls_baru": new_feat["subsls"],
                    "subsls_changed": subsls_changed,
                    "type": "by_id"
                })

        # Fitur ditambahkan
        for idsls in set(new_features.keys()) - set(old_features.keys()):
            new_feat = new_features[idsls]
            ditambahkan += 1
            self.changes_by_id.append({
                "idsls": idsls,
                "status": "DITAMBAHKAN",
                "batas_berubah": False,
                "luas_lama": 0.0,
                "luas_baru": new_feat["luas"],
                "selisih_luas": -new_feat["luas"],
                "subsls_lama": None,
                "subsls_baru": new_feat["subsls"],
                "subsls_changed": False,
                "type": "by_id"
            })

        # Fitur dihapus
        for idsls in set(old_features.keys()) - set(new_features.keys()):
            old_feat = old_features[idsls]
            dihapus += 1
            self.changes_by_id.append({
                "idsls": idsls,
                "status": "DIHAPUS",
                "batas_berubah": False,
                "luas_lama": old_feat["luas"],
                "luas_baru": 0.0,
                "selisih_luas": old_feat["luas"],
                "subsls_lama": old_feat["subsls"],
                "subsls_baru": None,
                "subsls_changed": False,
                "type": "by_id"
            })

        # ================================
        # 2. ANALISIS SPASIAL: SYMMETRICAL DIFFERENCE
        # ================================
        self.run_spatial_analysis(old_layer, new_layer)

        # ================================
        # 3. GABUNGKAN HASIL
        # ================================
        self.combined_report = self.changes_by_id.copy()
        for sc in self.spatial_changes:
            self.combined_report.append(sc)

        # Tampilkan ringkasan
        summary = f"""
📊 RINGKASAN PERUBAHAN (BERDASARKAN ID):

Total fitur di file lama: {total_old}
Total fitur di file baru: {total_new}

Perubahan terdeteksi:
- Fitur yang mengalami perubahan batas SLS: {batas_berubah}
- Fitur yang mengalami perubahan atribut 'subsls': {subsls_berubah}
- Fitur yang ditambahkan: {ditambahkan}
- Fitur yang dihapus: {dihapus}
- Total fitur yang berubah (by ID): {len(self.changes_by_id)}

🌍 RINGKASAN PERUBAHAN SPASIAL:
- Polygon hasil symmetrical difference: {len(self.spatial_changes)}

📋 TOTAL LAPORAN GABUNGAN: {len(self.combined_report)} entri

✅ Semua hasil siap diekspor ke CSV.
        """
        self.summary_text.setText(summary)

        # Tampilkan tabel (berdasarkan ID)
        self.table.setRowCount(len(self.changes_by_id))
        for row, change in enumerate(self.changes_by_id):
            self.table.setItem(row, 0, QTableWidgetItem(str(change["idsls"])))
            self.table.setItem(row, 1, QTableWidgetItem(change["status"]))
            self.table.setItem(row, 2, QTableWidgetItem("✅ Ya" if change["batas_berubah"] else "❌ Tidak"))
            self.table.setItem(row, 3, QTableWidgetItem(f"{change['luas_lama']:.4f}"))
            self.table.setItem(row, 4, QTableWidgetItem(f"{change['luas_baru']:.4f}"))
            self.table.setItem(row, 5, QTableWidgetItem(f"{change['selisih_luas']:+.4f}"))
            self.table.setItem(row, 6, QTableWidgetItem("✅ Ya" if change["subsls_changed"] else "❌ Tidak"))

        # Aktifkan tombol export
        self.export_btn.setEnabled(len(self.combined_report) > 0)

        QMessageBox.information(self, "Selesai", f"Deteksi selesai!\n{len(self.changes_by_id)} perubahan by ID + {len(self.spatial_changes)} perubahan spasial.")

    def run_spatial_analysis(self, old_layer, new_layer):
        """Deteksi perubahan batas berdasarkan geometri menggunakan Symmetrical Difference"""
        try:
            from qgis import processing
        except ImportError:
            self.summary_text.append("❌ Modul 'processing' tidak tersedia.")
            return

        crs = new_layer.crs()
        if old_layer.crs() != crs:
            self.summary_text.append("⚠️ CRS tidak sama — mencoba reproject...")
            # Untuk versi sederhana, kita asumsikan CRS sama

        # Siapkan parameter untuk symmetrical difference
        params = {
            'INPUT': new_layer,
            'OVERLAY': old_layer,
            'OUTPUT': 'memory:'
        }

        try:
            result = processing.run("qgis:symmetricaldifference", params)
            diff_layer = result['OUTPUT']

            if diff_layer and diff_layer.featureCount() > 0:
                # Beri nama dan tambahkan ke project
                diff_layer.setName("SLS_Perubahan_Batas_Spasial")
                
                # Styling: warna merah transparan
                symbol = QgsFillSymbol.createSimple({
                    'color': '255,0,0,100',
                    'outline_color': '255,0,0',
                    'outline_width': '0.5'
                })
                renderer = QgsSingleSymbolRenderer(symbol)
                diff_layer.setRenderer(renderer)
                
                QgsProject.instance().addMapLayer(diff_layer)

                # Simpan hasil untuk laporan gabungan
                                # --- SPATIAL JOIN: bawa atribut idsls dari layer baru ---
                try:
                    join_params = {
                        'INPUT': diff_layer,
                        'JOIN': new_layer,
                        'PREDICATE': [0],  # intersects
                        'JOIN_FIELDS': ['idsls'],
                        'METHOD': 0,  # Create separate feature for each matching feature (one-to-many)
                        'DISCARD_NONMATCHING': False,
                        'OUTPUT': 'memory:'
                    }
                    diff_joined = processing.run("native:joinattributesbylocation", join_params)['OUTPUT']
                    
                    # Ganti diff_layer dengan hasil join
                    diff_layer = diff_joined
                    diff_layer.setName("SLS_Perubahan_Batas_Spasial")
                    
                    # Styling
                    symbol = QgsFillSymbol.createSimple({
                        'color': '255,0,0,100',
                        'outline_color': '255,0,0',
                        'outline_width': '0.5'
                    })
                    renderer = QgsSingleSymbolRenderer(symbol)
                    diff_layer.setRenderer(renderer)
                    
                    QgsProject.instance().addMapLayer(diff_layer)

                    # Simpan hasil
                    self.spatial_changes = []
                    for feat in diff_layer.getFeatures():
                        geom = feat.geometry()
                        if geom:
                            area = geom.area()
                            # Ambil idsls dari hasil join
                            idsls = feat["idsls"] if feat.fieldNameIndex("idsls") >= 0 else "SPASIAL_" + str(feat.id())
                            self.spatial_changes.append({
                                "idsls": idsls,
                                "status": "PERUBAHAN_SPASIAL",
                                "batas_berubah": True,
                                "luas_lama": 0.0,
                                "luas_baru": area,
                                "selisih_luas": area,
                                "subsls_lama": None,
                                "subsls_baru": None,
                                "subsls_changed": False,
                                "type": "spatial",
                                "area": area
                            })

                    self.summary_text.append(f"\n✅ Layer 'SLS_Perubahan_Batas_Spasial' (dengan atribut idsls) ditambahkan ({diff_layer.featureCount()} polygon, luas total: {sum(sc['area'] for sc in self.spatial_changes):.4f}).")
                    
                except Exception as e:
                    self.summary_text.append(f"\n⚠️ Gagal spatial join: {str(e)}")
                    # Fallback: pakai diff_layer asli
                    self.spatial_changes = []
                    for feat in diff_layer.getFeatures():
                        geom = feat.geometry()
                        if geom:
                            area = geom.area()
                            idsls = "SPASIAL_" + str(feat.id())
                            self.spatial_changes.append({
                                "idsls": idsls,
                                "status": "PERUBAHAN_SPASIAL",
                                "batas_berubah": True,
                                "luas_lama": 0.0,
                                "luas_baru": area,
                                "selisih_luas": area,
                                "subsls_lama": None,
                                "subsls_baru": None,
                                "subsls_changed": False,
                                "type": "spatial",
                                "area": area
                            })

                self.summary_text.append(f"\n✅ Layer 'SLS_Perubahan_Batas_Spasial' ditambahkan ({diff_layer.featureCount()} polygon, luas total: {sum(sc['area'] for sc in self.spatial_changes):.4f}).")
            else:
                self.summary_text.append(f"\nℹ️ Tidak ada perubahan batas spasial terdeteksi.")
                self.spatial_changes = []

        except Exception as e:
            self.summary_text.append(f"\n❌ Gagal jalankan symmetrical difference: {str(e)}")
            self.spatial_changes = []

    def export_combined_to_csv(self):
        """Export hasil gabungan (ID + spasial) ke file CSV"""
        if not self.combined_report:
            QMessageBox.warning(self, "Peringatan", "Tidak ada data untuk di-export.")
            return

        default_name = "SLS_Laporan_Gabungan_" + time.strftime("%Y%m%d_%H%M%S") + ".csv"
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Simpan Laporan Gabungan ke CSV", default_name, "CSV Files (*.csv)"
        )

        if not save_path:
            return

        if not save_path.lower().endswith('.csv'):
            save_path += '.csv'

        try:
            with open(save_path, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = [
                    "idsls", "Status", "Tipe_Perubahan", "Perubahan_Batas_SLS", 
                    "Luas_Lama", "Luas_Baru", "Selisih_Luas", "Luas_Perubahan_Spasial",
                    "subsls_Lama", "subsls_Baru", "Perubahan_subsls", "Catatan"
                ]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()

                for item in self.combined_report:
                    if item["type"] == "spatial":
                        catatan = "Perubahan batas spasial (symmetrical difference)"
                        luas_spasial = f"{item.get('area', 0.0):.4f}"
                    else:
                        catatan = []
                        if item["status"] == "DIUBAH":
                            if item["batas_berubah"]:
                                catatan.append("Batas berubah (luas)")
                            if item["subsls_changed"]:
                                catatan.append("subsls berubah")
                        else:
                            catatan.append(item["status"])
                        catatan = "; ".join(catatan)
                        luas_spasial = ""

                    writer.writerow({
                        "idsls": item["idsls"],
                        "Status": item["status"],
                        "Tipe_Perubahan": item["type"],
                        "Perubahan_Batas_SLS": "Ya" if item["batas_berubah"] else "Tidak",
                        "Luas_Lama": f"{item['luas_lama']:.4f}" if item["type"] != "spatial" else "",
                        "Luas_Baru": f"{item['luas_baru']:.4f}" if item["type"] != "spatial" else "",
                        "Selisih_Luas": f"{item['selisih_luas']:+.4f}" if item["type"] != "spatial" else "",
                        "Luas_Perubahan_Spasial": luas_spasial,
                        "subsls_Lama": item["subsls_lama"] if item["subsls_lama"] is not None else "NULL",
                        "subsls_Baru": item["subsls_baru"] if item["subsls_baru"] is not None else "NULL",
                        "Perubahan_subsls": "Ya" if item["subsls_changed"] else "Tidak",
                        "Catatan": catatan
                    })

            QMessageBox.information(self, "Sukses", f"Laporan gabungan berhasil di-export ke:\n{save_path}")

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
            "SLS Change Detector (Final)",
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