# sls_change_detector.py
# Plugin QGIS untuk deteksi perubahan SLS berdasarkan idsubsls
# - Primary key: idsubsls


from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QMessageBox, QTableWidget, QTableWidgetItem,
    QGroupBox, QGridLayout, QDialogButtonBox, QTextEdit, QTabWidget, QToolBar,
    QWidget, QProgressBar
)
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt, QDateTime, QTimer, QVariant
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsField, QgsFields,
    QgsVectorFileWriter, QgsCoordinateReferenceSystem, QgsWkbTypes,
    QgsFeatureRequest, QgsVectorLayerUtils, QgsFillSymbol, QgsSingleSymbolRenderer
)
from qgis.utils import iface
import os
import time
import csv
import logging


class SLSChangeDetectorDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SLS Change Detector - idsubsls")
        self.resize(1000, 800)

        self.changes_by_id = []
        self.spatial_changes = []
        self.combined_report = []
        self.duplicate_ids_new = []  # Duplikat berdasarkan idsubsls

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

        # Progress Bar
        self.progress = QProgressBar()
        self.progress.setMaximum(100)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # Button: Run
        self.run_btn = QPushButton("Deteksi Perubahan + Validasi")
        self.run_btn.clicked.connect(self.run_detection)
        layout.addWidget(self.run_btn)

        # Tab Widget: Results
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Tab 1: Summary
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.tabs.addTab(self.summary_text, "Ringkasan")

        # Tab 2: Changed Features (by idsubsls)
        table_layout = QVBoxLayout()
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "idsubsls", "Status", "Perubahan Batas SLS", "Luas Lama", "Luas Baru", "Selisih Luas", "kdsubsls Berubah"
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        table_layout.addWidget(self.table)

        # Tombol Export
        export_layout = QHBoxLayout()
        self.export_csv_btn = QPushButton("Export ke CSV")
        self.export_csv_btn.clicked.connect(self.export_combined_to_csv)
        self.export_csv_btn.setEnabled(False)
        
        self.export_gpkg_btn = QPushButton("Export ke GeoPackage")
        self.export_gpkg_btn.clicked.connect(self.export_to_geopackage)
        self.export_gpkg_btn.setEnabled(False)
        
        export_layout.addWidget(self.export_csv_btn)
        export_layout.addWidget(self.export_gpkg_btn)
        table_layout.addLayout(export_layout)

        table_widget = QWidget()
        table_widget.setLayout(table_layout)
        self.tabs.addTab(table_widget, "Perubahan Berdasarkan idsubsls")

        # Setup logging
        self.setup_logging()

    def setup_logging(self):
        """Setup logging untuk debug"""
        log_dir = os.path.dirname(__file__)
        log_file = os.path.join(log_dir, 'sls_detector.log')
        logging.basicConfig(
            filename=log_file,
            level=logging.DEBUG,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
    
    def log_detection_info(self, message):
        """Log info message"""
        logging.info(message)
        self.summary_text.append(f"ℹ️ {message}")

    def select_old_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Pilih File SLS Lama", "", "GeoPackage (*.gpkg)")
        if file_path:
            self.old_line.setText(file_path)

    def select_new_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Pilih File SLS Baru", "", "GeoPackage (*.gpkg)")
        if file_path:
            self.new_line.setText(file_path)

    def validate_layers(self, old_layer, new_layer):
        """Validasi komprehensif kedua layer"""
        errors = []
        
        # Cek CRS
        if not old_layer.crs().isValid() or not new_layer.crs().isValid():
            errors.append("CRS salah satu layer tidak valid")
        
        # Cek tipe geometri
        if old_layer.geometryType() != new_layer.geometryType():
            errors.append("Tipe geometri layer tidak sama")
        
        # Cek field required
        required_fields = ["idsubsls", "luas"]
        for field in required_fields:
            old_idx = old_layer.fields().indexOf(field)
            new_idx = new_layer.fields().indexOf(field)
            if old_idx == -1 or new_idx == -1:
                errors.append(f"Field '{field}' tidak ditemukan di salah satu layer")
        
        # Cek apakah layers memiliki fitur
        if old_layer.featureCount() == 0:
            errors.append("Layer lama tidak memiliki fitur")
        if new_layer.featureCount() == 0:
            errors.append("Layer baru tidak memiliki fitur")
        
        return errors

    def detect_geometry_changes(self, old_feat, new_feat):
        """Deteksi perubahan geometri yang lebih akurat"""
        try:
            geom_old = old_feat.geometry()
            geom_new = new_feat.geometry()
            
            if not geom_old.isGeosValid() or not geom_new.isGeosValid():
                return False, 0.0
            
            # Gunakan tolerance untuk perbandingan
            tolerance = 0.001
            geometri_sama = geom_old.equals(geom_new, tolerance)
            
            # Hitung selisih luas dengan handling error
            try:
                luas_old = geom_old.area() if geom_old else 0.0
                luas_new = geom_new.area() if geom_new else 0.0
                selisih_luas = abs(luas_old - luas_new)
            except:
                selisih_luas = 0.0
            
            # Threshold perubahan (bisa disesuaikan)
            threshold_luas = 1.0  # 1 meter persegi
            perubahan_signifikan = selisih_luas > threshold_luas
            
            return not geometri_sama or perubahan_signifikan, selisih_luas
            
        except Exception as e:
            self.log_detection_info(f"Error deteksi geometri: {str(e)}")
            return False, 0.0

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

        # Show progress
        self.progress.setVisible(True)
        self.progress.setValue(10)

        # Load layers
        old_layer = QgsVectorLayer(old_path, "sls_lama", "ogr")
        new_layer = QgsVectorLayer(new_path, "sls_baru", "ogr")

        if not old_layer.isValid() or not new_layer.isValid():
            QMessageBox.critical(self, "Error", "Gagal membuka salah satu file GeoPackage!")
            self.progress.setVisible(False)
            return

        # Validasi layers
        self.progress.setValue(20)
        validation_errors = self.validate_layers(old_layer, new_layer)
        if validation_errors:
            QMessageBox.critical(self, "Error Validasi", "\n".join(validation_errors))
            self.progress.setVisible(False)
            return

        # Cek field yang dibutuhkan
        required_fields = ["idsubsls", "luas"]
        for field in required_fields:
            if field not in [f.name() for f in old_layer.fields()] or field not in [f.name() for f in new_layer.fields()]:
                QMessageBox.critical(self, "Error", f"Field '{field}' tidak ditemukan di salah satu layer!")
                self.progress.setVisible(False)
                return

        # ================================
        # 0. VALIDASI: CEK DUPLIKAT DI FILE BARU (berdasarkan gid + kdsubsls = NULL)
        # ================================
        self.progress.setValue(30)
        self.duplicate_ids_new = []
        if "gid" in [f.name() for f in new_layer.fields()] and "kdsubsls" in [f.name() for f in new_layer.fields()]:
            gid_groups = {}
            for feat in new_layer.getFeatures():
                gid = feat["gid"]
                kdsubsls = feat["kdsubsls"]
                idsubsls = feat["idsubsls"]

                if gid not in gid_groups:
                    gid_groups[gid] = []
                gid_groups[gid].append({"idsubsls": idsubsls, "kdsubsls": kdsubsls})

            for gid, features in gid_groups.items():
                if len(features) > 1:
                    has_null = any(f["kdsubsls"] is None or str(f["kdsubsls"]).strip() == "" for f in features)
                    if has_null:
                        for f in features:
                            self.duplicate_ids_new.append(f["idsubsls"])

            if self.duplicate_ids_new:
                self.summary_text.append(
                    f"\n⚠️ VALIDASI: Ditemukan {len(self.duplicate_ids_new)} idsubsls duplikat di file BARU.\n"
                    "Kriteria: gid sama + ada kdsubsls = NULL → dianggap belum lengkap.\n"
                    f"Contoh: {', '.join(self.duplicate_ids_new[:5])}"
                )
        else:
            self.summary_text.append("\nℹ️ Field 'gid' atau 'kdsubsls' tidak ditemukan di file baru — lewati validasi duplikat.")

        # ================================
        # 1. ANALISIS BERDASARKAN idsubsls
        # ================================
        self.progress.setValue(40)
        self.changes_by_id = []
        old_features = {}
        for feat in old_layer.getFeatures():
            idsubsls = feat["idsubsls"]
            old_features[idsubsls] = {
                "geom": feat.geometry(),
                "luas": float(feat["luas"]) if feat["luas"] is not None else 0.0,
                "kdsubsls": feat["kdsubsls"] if "kdsubsls" in feat.fields().names() else None,
                "feature": feat  # Simpan feature lengkap untuk analisis geometri
            }

        new_features = {}
        for feat in new_layer.getFeatures():
            idsubsls = feat["idsubsls"]
            new_features[idsubsls] = {
                "geom": feat.geometry(),
                "luas": float(feat["luas"]) if feat["luas"] is not None else 0.0,
                "kdsubsls": feat["kdsubsls"] if "kdsubsls" in feat.fields().names() else None,
                "feature": feat  # Simpan feature lengkap untuk analisis geometri
            }

        total_old = len(old_features)
        total_new = len(new_features)
        batas_berubah = 0
        kdsubsls_berubah = 0
        ditambahkan = 0
        dihapus = 0

        # Fitur yang ada di kedua file
        self.progress.setValue(50)
        for idsubsls in set(old_features.keys()) & set(new_features.keys()):
            old_feat = old_features[idsubsls]
            new_feat = new_features[idsubsls]

            # Deteksi perubahan geometri yang lebih akurat
            batas_sls_berubah, selisih_luas_geom = self.detect_geometry_changes(
                old_feat["feature"], new_feat["feature"]
            )
            
            # Juga bandingkan luas dari atribut
            selisih_luas_attr = old_feat["luas"] - new_feat["luas"]
            kdsubsls_changed = old_feat["kdsubsls"] != new_feat["kdsubsls"]

            if batas_sls_berubah or kdsubsls_changed or abs(selisih_luas_attr) > 0.001:
                status = "DIUBAH"
                if batas_sls_berubah:
                    batas_berubah += 1
                if kdsubsls_changed:
                    kdsubsls_berubah += 1

                self.changes_by_id.append({
                    "idsubsls": idsubsls,
                    "status": status,
                    "batas_berubah": batas_sls_berubah,
                    "luas_lama": old_feat["luas"],
                    "luas_baru": new_feat["luas"],
                    "selisih_luas": selisih_luas_attr,
                    "kdsubsls_lama": old_feat["kdsubsls"],
                    "kdsubsls_baru": new_feat["kdsubsls"],
                    "kdsubsls_changed": kdsubsls_changed,
                    "type": "by_id",
                    "geom": new_feat["geom"]  # Simpan geometri untuk export
                })

        # Fitur ditambahkan (ada di baru, tidak di lama)
        self.progress.setValue(60)
        for idsubsls in set(new_features.keys()) - set(old_features.keys()):
            new_feat = new_features[idsubsls]
            ditambahkan += 1
            self.changes_by_id.append({
                "idsubsls": idsubsls,
                "status": "DITAMBAHKAN",
                "batas_berubah": False,
                "luas_lama": 0.0,
                "luas_baru": new_feat["luas"],
                "selisih_luas": -new_feat["luas"],
                "kdsubsls_lama": None,
                "kdsubsls_baru": new_feat["kdsubsls"],
                "kdsubsls_changed": False,
                "type": "by_id",
                "geom": new_feat["geom"]
            })

        # Fitur dihapus (ada di lama, tidak di baru)
        for idsubsls in set(old_features.keys()) - set(new_features.keys()):
            old_feat = old_features[idsubsls]
            dihapus += 1
            self.changes_by_id.append({
                "idsubsls": idsubsls,
                "status": "DIHAPUS",
                "batas_berubah": False,
                "luas_lama": old_feat["luas"],
                "luas_baru": 0.0,
                "selisih_luas": old_feat["luas"],
                "kdsubsls_lama": old_feat["kdsubsls"],
                "kdsubsls_baru": None,
                "kdsubsls_changed": False,
                "type": "by_id",
                "geom": None
            })

        # ================================
        # 2. ANALISIS SPASIAL
        # ================================
        self.progress.setValue(70)
        self.run_spatial_analysis(old_layer, new_layer)

        # ================================
        # 3. GABUNGKAN HASIL
        # ================================
        self.progress.setValue(80)
        self.combined_report = self.changes_by_id.copy()
        for sc in self.spatial_changes:
            self.combined_report.append(sc)

        # Tampilkan ringkasan
        summary = f"""
📊 RINGKASAN PERUBAHAN (BERDASARKAN idsubsls):

Total fitur di file lama: {total_old}
Total fitur di file baru: {total_new}

Perubahan terdeteksi:
- Fitur yang mengalami perubahan batas SLS: {batas_berubah}
- Fitur yang mengalami perubahan atribut 'kdsubsls': {kdsubsls_berubah}
- Fitur yang ditambahkan: {ditambahkan}
- Fitur yang dihapus: {dihapus}
- Total fitur yang berubah (by idsubsls): {len(self.changes_by_id)}

🌍 RINGKASAN PERUBAHAN SPASIAL:
- Polygon hasil symmetrical difference: {len(self.spatial_changes)}

📋 TOTAL LAPORAN GABUNGAN: {len(self.combined_report)} entri

⏰ Waktu analisis: {time.strftime('%Y-%m-%d %H:%M:%S')}
        """
        self.summary_text.setText(summary)

        # Tampilkan tabel
        self.progress.setValue(90)
        self.table.setRowCount(len(self.changes_by_id))
        for row, change in enumerate(self.changes_by_id):
            self.table.setItem(row, 0, QTableWidgetItem(str(change["idsubsls"])))
            self.table.setItem(row, 1, QTableWidgetItem(change["status"]))
            self.table.setItem(row, 2, QTableWidgetItem("✅ Ya" if change["batas_berubah"] else "❌ Tidak"))
            self.table.setItem(row, 3, QTableWidgetItem(f"{change['luas_lama']:.4f}"))
            self.table.setItem(row, 4, QTableWidgetItem(f"{change['luas_baru']:.4f}"))
            self.table.setItem(row, 5, QTableWidgetItem(f"{change['selisih_luas']:+.4f}"))
            self.table.setItem(row, 6, QTableWidgetItem("✅ Ya" if change["kdsubsls_changed"] else "❌ Tidak"))

        self.export_csv_btn.setEnabled(len(self.combined_report) > 0)
        self.export_gpkg_btn.setEnabled(len(self.combined_report) > 0)
        
        self.progress.setValue(100)
        QMessageBox.information(self, "Selesai", f"Deteksi selesai!\n{len(self.changes_by_id)} perubahan by idsubsls + {len(self.spatial_changes)} perubahan spasial.")
        
        # Hide progress after completion
        QTimer.singleShot(1000, lambda: self.progress.setVisible(False))

    def run_spatial_analysis(self, old_layer, new_layer):
        """Deteksi perubahan batas berdasarkan geometri"""
        try:
            from qgis import processing
        except ImportError:
            self.summary_text.append("❌ Modul 'processing' tidak tersedia.")
            return

        if old_layer.crs() != new_layer.crs():
            self.log_detection_info("CRS berbeda — reproject layer baru...")
            params = {
                'INPUT': new_layer,
                'TARGET_CRS': old_layer.crs(),
                'OUTPUT': 'memory:'
            }
            new_layer = processing.run("native:reprojectlayer", params)['OUTPUT']

        # Enhanced spatial analysis dengan multiple methods
        spatial_results = self.enhanced_spatial_analysis(old_layer, new_layer)
        
        if spatial_results.get('symmetrical'):
            diff_layer = spatial_results['symmetrical']
            
            join_params = {
                'INPUT': diff_layer,
                'JOIN': new_layer,
                'PREDICATE': [0],
                'JOIN_FIELDS': ['idsubsls'],
                'METHOD': 0,
                'DISCARD_NONMATCHING': False,
                'OUTPUT': 'memory:'
            }
            diff_joined = processing.run("native:joinattributesbylocation", join_params)['OUTPUT']
            diff_joined.setName("SLS_Perubahan_Batas_Spasial")

            symbol = QgsFillSymbol.createSimple({
                'color': '255,0,0,100',
                'outline_color': '255,0,0',
                'outline_width': '0.5'
            })
            renderer = QgsSingleSymbolRenderer(symbol)
            diff_joined.setRenderer(renderer)
            QgsProject.instance().addMapLayer(diff_joined)

            self.spatial_changes = []
            for feat in diff_joined.getFeatures():
                geom = feat.geometry()
                if geom:
                    area = geom.area()
                    idsubsls = feat["idsubsls"] if feat.fieldNameIndex("idsubsls") >= 0 else "SPASIAL_" + str(feat.id())
                    self.spatial_changes.append({
                        "idsubsls": idsubsls,
                        "status": "PERUBAHAN_SPASIAL",
                        "batas_berubah": True,
                        "luas_lama": 0.0,
                        "luas_baru": area,
                        "selisih_luas": area,
                        "kdsubsls_lama": None,
                        "kdsubsls_baru": None,
                        "kdsubsls_changed": False,
                        "type": "spatial",
                        "area": area,
                        "geom": geom
                    })

            self.log_detection_info(f"Layer 'SLS_Perubahan_Batas_Spasial' ditambahkan ({diff_joined.featureCount()} polygon, luas total: {sum(sc['area'] for sc in self.spatial_changes):.4f}).")
        else:
            self.log_detection_info("Tidak ada perubahan batas spasial terdeteksi.")
            self.spatial_changes = []

    def enhanced_spatial_analysis(self, old_layer, new_layer):
        """Analisis spasial dengan multiple methods"""
        try:
            from qgis import processing
            
            results = {}
            
            # Symmetrical Difference (existing)
            params = {
                'INPUT': new_layer,
                'OVERLAY': old_layer,
                'OUTPUT': 'memory:'
            }
            diff_layer = processing.run("native:symmetricaldifference", params)['OUTPUT']
            results['symmetrical'] = diff_layer
            
            # Difference: New - Old (tambahan di file baru)
            params_diff_new = {
                'INPUT': new_layer,
                'OVERLAY': old_layer,
                'OUTPUT': 'memory:'
            }
            diff_new = processing.run("native:difference", params_diff_new)['OUTPUT']
            results['added_areas'] = diff_new
            
            # Difference: Old - New (yang hilang di file baru)
            params_diff_old = {
                'INPUT': old_layer,
                'OVERLAY': new_layer,
                'OUTPUT': 'memory:'
            }
            diff_old = processing.run("native:difference", params_diff_old)['OUTPUT']
            results['removed_areas'] = diff_old
            
            # Tambahkan layer tambahan ke peta
            if diff_new.featureCount() > 0:
                diff_new.setName("SLS_Area_Tambahan")
                symbol_add = QgsFillSymbol.createSimple({
                    'color': '0,255,0,100',
                    'outline_color': '0,255,0',
                    'outline_width': '0.5'
                })
                diff_new.setRenderer(QgsSingleSymbolRenderer(symbol_add))
                QgsProject.instance().addMapLayer(diff_new)
                
            if diff_old.featureCount() > 0:
                diff_old.setName("SLS_Area_Dihapus")
                symbol_remove = QgsFillSymbol.createSimple({
                    'color': '255,255,0,100',
                    'outline_color': '255,255,0',
                    'outline_width': '0.5'
                })
                diff_old.setRenderer(QgsSingleSymbolRenderer(symbol_remove))
                QgsProject.instance().addMapLayer(diff_old)
            
            return results
            
        except Exception as e:
            self.log_detection_info(f"Error analisis spasial: {str(e)}")
            return {}

    def export_combined_to_csv(self):
        """Export hasil gabungan ke CSV"""
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
                    "idsubsls", "Status", "Tipe_Perubahan", "Perubahan_Batas_SLS",
                    "Luas_Lama", "Luas_Baru", "Selisih_Luas", "Luas_Perubahan_Spasial",
                    "kdsubsls_Lama", "kdsubsls_Baru", "Perubahan_kdsubsls", "Duplikat_File_Baru", "Catatan"
                ]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()

                for item in self.combined_report:
                    is_duplicate = "Ya" if item["idsubsls"] in self.duplicate_ids_new else "Tidak"
                    
                    if item["type"] == "spatial":
                        catatan = "Perubahan batas spasial (symmetrical difference)"
                        luas_spasial = f"{item.get('area', 0.0):.4f}"
                    else:
                        catatan = []
                        if item["status"] == "DIUBAH":
                            if item["batas_berubah"]:
                                catatan.append("Batas berubah (luas)")
                            if item["kdsubsls_changed"]:
                                catatan.append("kdsubsls berubah")
                        else:
                            catatan.append(item["status"])
                        catatan = "; ".join(catatan)
                        luas_spasial = ""

                    writer.writerow({
                        "idsubsls": item["idsubsls"],
                        "Status": item["status"],
                        "Tipe_Perubahan": item["type"],
                        "Perubahan_Batas_SLS": "Ya" if item["batas_berubah"] else "Tidak",
                        "Luas_Lama": f"{item['luas_lama']:.4f}" if item["type"] != "spatial" else "",
                        "Luas_Baru": f"{item['luas_baru']:.4f}" if item["type"] != "spatial" else "",
                        "Selisih_Luas": f"{item['selisih_luas']:+.4f}" if item["type"] != "spatial" else "",
                        "Luas_Perubahan_Spasial": luas_spasial,
                        "kdsubsls_Lama": item["kdsubsls_lama"] if item["kdsubsls_lama"] is not None else "NULL",
                        "kdsubsls_Baru": item["kdsubsls_baru"] if item["kdsubsls_baru"] is not None else "NULL",
                        "Perubahan_kdsubsls": "Ya" if item["kdsubsls_changed"] else "Tidak",
                        "Duplikat_File_Baru": is_duplicate,
                        "Catatan": catatan
                    })

            QMessageBox.information(self, "Sukses", f"Laporan gabungan berhasil di-export ke:\n{save_path}")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Gagal export ke CSV:\n{str(e)}")

    def export_to_geopackage(self):
        """Export hasil ke GeoPackage"""
        if not self.combined_report:
            QMessageBox.warning(self, "Peringatan", "Tidak ada data untuk di-export.")
            return

        save_path, _ = QFileDialog.getSaveFileName(
            self, "Simpan ke GeoPackage", "", "GeoPackage (*.gpkg)"
        )

        if not save_path:
            return

        try:
            # Buat layer output
            fields = QgsFields()
            fields.append(QgsField("idsubsls", QVariant.String))
            fields.append(QgsField("status", QVariant.String))
            fields.append(QgsField("tipe_perubahan", QVariant.String))
            fields.append(QgsField("batas_berubah", QVariant.String))
            fields.append(QgsField("luas_lama", QVariant.Double))
            fields.append(QgsField("luas_baru", QVariant.Double))
            fields.append(QgsField("selisih_luas", QVariant.Double))
            fields.append(QgsField("kdsubsls_lama", QVariant.String))
            fields.append(QgsField("kdsubsls_baru", QVariant.String))
            fields.append(QgsField("kdsubsls_changed", QVariant.String))
            fields.append(QgsField("duplikat", QVariant.String))
            
            crs = QgsProject.instance().crs()
            writer = QgsVectorFileWriter(
                save_path, 
                "UTF-8", 
                fields, 
                QgsWkbTypes.Polygon, 
                crs, 
                "GPKG"
            )
            
            if writer.hasError() != QgsVectorFileWriter.NoError:
                QMessageBox.critical(self, "Error", f"Error membuat file: {writer.errorMessage()}")
                return
                
            # Tambahkan features yang memiliki geometri
            features_added = 0
            for change in self.combined_report:
                if change.get('geom') and change['geom'] is not None:
                    feat = QgsFeature()
                    feat.setGeometry(change['geom'])
                    
                    is_duplicate = "Ya" if change["idsubsls"] in self.duplicate_ids_new else "Tidak"
                    
                    feat.setAttributes([
                        change['idsubsls'],
                        change['status'],
                        change['type'],
                        "Ya" if change["batas_berubah"] else "Tidak",
                        change['luas_lama'],
                        change['luas_baru'],
                        change['selisih_luas'],
                        str(change['kdsubsls_lama']) if change['kdsubsls_lama'] is not None else "NULL",
                        str(change['kdsubsls_baru']) if change['kdsubsls_baru'] is not None else "NULL",
                        "Ya" if change["kdsubsls_changed"] else "Tidak",
                        is_duplicate
                    ])
                    writer.addFeature(feat)
                    features_added += 1
            
            del writer  # Important: close the writer
            
            if features_added > 0:
                QMessageBox.information(self, "Sukses", 
                    f"Data berhasil di-export ke GeoPackage!\n"
                    f"Total features: {features_added}\n"
                    f"File: {save_path}")
            else:
                QMessageBox.warning(self, "Peringatan", 
                    "Tidak ada features dengan geometri yang bisa di-export.")
                
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Gagal export ke GeoPackage:\n{str(e)}")


class SLSChangeDetectorPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, 'icon.png')
        self.action = QAction(
            QIcon(icon_path) if os.path.exists(icon_path) else QIcon(),
            "SLS Change Detector (idsubsls)",
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


# Untuk testing standalone
if __name__ == "__main__":
    # Testing code
    pass