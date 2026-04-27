import os
import sys
import logging
import cv2

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QTextEdit,
    QDialog, QDoubleSpinBox, QDialogButtonBox, QFormLayout,
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap

from weld_controller import WeldController
from state_machine import Event

log = logging.getLogger(__name__)

_CONTROLLER_IMG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "images", "Controller Layout.png"
)

_MANUAL_CONTROLLER_IMG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "images", "manual_controller.png"
)

_STATE_DISPLAY = {
    "SYSTEM_INIT":            ("Initializing",   "System"),
    "IDLE":                   ("Idle",           "Manual"),
    "MANUAL_JOG":             ("Jogging",        "Manual"),
    "CAMERA_LASER_TARGETING": ("Camera/Laser",   "Manual"),
    "SET_WELD_POINT":         ("Saving Point",   "Manual"),
    "MOVE_TO_POSITION":       ("Moving",         "Auto"),
    "FINE_POSITIONING":       ("Fine Pos.",      "Auto"),
    "Z_LOWERING":             ("Z Lower",        "Auto"),
    "EXECUTE_WELD":           ("Welding",        "Auto"),
    "Z_RAISING":              ("Z Raise",        "Auto"),
    "EMERGENCY_STOP":         ("E-STOP",         "E-Stop"),
    "ERROR":                  ("Error",          "Error"),
    "MANUAL_WELD": ("Manual Weld", "Manual"),
}


class SpotWelderGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Spot Welder Control GUI")
        self.resize(1200, 800)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.operations_tab = QWidget()
        self.waypoints_tab = QWidget()
        self.manual_weld_tab = QWidget()
        
        self.tabs.addTab(self.operations_tab, "Operations")
        self.tabs.addTab(self.waypoints_tab, "Waypoints")
        self.tabs.addTab(self.manual_weld_tab, "Manual Weld")

        self.build_operations_tab()
        self.build_waypoints_tab()
        self.build_manual_weld_tab()

        self.controller = WeldController(
            enable_grbl=False,
            enable_homing=False,
            enable_force_sensor=False,
            enable_controller=False,
            enable_camera=False,
            enable_laser=False,
            enable_weld_relay=False,
        )
        self._travel_height_prompted = False
        self._connect_controller()
        self.controller.start()

    # ------------------------------------------------------------------
    # Tab builders
    # ------------------------------------------------------------------
    def build_operations_tab(self):
        main_layout = QVBoxLayout()
        top_layout = QHBoxLayout()

        # Camera feed
        camera_group = QGroupBox("Camera View")
        camera_layout = QVBoxLayout()

        self.camera_label = QLabel("Camera Feed")
        self.camera_label.setAlignment(Qt.AlignCenter)
        self.camera_label.setMinimumSize(640, 480)
        self.camera_label.setStyleSheet(
            "background-color: black; color: white; border: 1px solid black;"
        )

        camera_layout.addWidget(self.camera_label)
        camera_group.setLayout(camera_layout)

        # Right-side panel
        right_panel = QVBoxLayout()

        status_group = QGroupBox("Machine Status")
        status_layout = QVBoxLayout()

        self.state_label = QLabel("State: Initializing")
        self.mode_label = QLabel("Mode: System")
        self.pose_x_label = QLabel("Pose X: 0.00")
        self.pose_y_label = QLabel("Pose Y: 0.00")
        self.pose_z_label = QLabel("Pose Z: 0.00")
        self.progress_label = QLabel("Waypoint Progress: 0 / 0")
        self.laser_label = QLabel("Laser: OFF")
        self.force_label = QLabel("Force Sensor: --")
        self.travel_height_label = QLabel("Travel Z: 25.0 mm")

        for lbl in (
            self.state_label,
            self.mode_label,
            self.pose_x_label,
            self.pose_y_label,
            self.pose_z_label,
            self.progress_label,
            self.laser_label,
            self.force_label,
            self.travel_height_label,
        ):
            status_layout.addWidget(lbl)

        status_group.setLayout(status_layout)

        controls_group = QGroupBox("Controls")
        controls_layout = QGridLayout()

        self.start_weld_seq_btn = QPushButton("Start Weld Sequence")
        self.pause_btn = QPushButton("Pause")
        self.estop_btn = QPushButton("E-Stop")
        self.home_btn = QPushButton("Go to Home Position")

        controls_layout.addWidget(self.start_weld_seq_btn, 0, 0)
        controls_layout.addWidget(self.pause_btn, 0, 1)
        controls_layout.addWidget(self.estop_btn, 1, 0, 1, 2)
        controls_layout.addWidget(self.home_btn, 2, 0, 1, 2)
        self.set_travel_height_btn = QPushButton("Set Travel Height…")
        controls_layout.addWidget(self.set_travel_height_btn, 3, 0, 1, 2)
        controls_group.setLayout(controls_layout)

        self.estop_btn.setStyleSheet(
            "background-color: red; color: white; font-weight: bold;"
        )
        self.pause_btn.setEnabled(False)

        controller_img_group = QGroupBox("Controller Layout")
        controller_img_layout = QVBoxLayout()
        controller_img_layout.addWidget(self._controller_image_label())
        controller_img_group.setLayout(controller_img_layout)

        right_panel.addWidget(status_group)
        right_panel.addWidget(controls_group)
        right_panel.addWidget(controller_img_group)

        top_layout.addWidget(camera_group, 2)
        top_layout.addLayout(right_panel, 1)

        log_group = QGroupBox("System Messages")
        log_layout = QVBoxLayout()
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        log_layout.addWidget(self.log_box)
        log_group.setLayout(log_layout)

        main_layout.addLayout(top_layout)
        main_layout.addWidget(log_group)
        self.operations_tab.setLayout(main_layout)

    def build_waypoints_tab(self):
        main_layout = QHBoxLayout()

        left_panel = QVBoxLayout()

        pose_group = QGroupBox("Current Pose")
        pose_layout = QVBoxLayout()
        self.wp_pose_x = QLabel("X: 0.00")
        self.wp_pose_y = QLabel("Y: 0.00")
        self.wp_pose_z = QLabel("Z: 0.00")
        pose_layout.addWidget(self.wp_pose_x)
        pose_layout.addWidget(self.wp_pose_y)
        pose_layout.addWidget(self.wp_pose_z)
        pose_group.setLayout(pose_layout)

        jog_group = QGroupBox("Jog Controls")
        jog_layout = QGridLayout()

        self.jog_yp = QPushButton("Y+")
        self.jog_xm = QPushButton("X-")
        self.jog_xp = QPushButton("X+")
        self.jog_ym = QPushButton("Y-")
        self.enter_jog_btn = QPushButton("Enter Jog Mode")
        self.exit_jog_btn = QPushButton("Exit Jog Mode")

        jog_layout.addWidget(self.jog_yp, 0, 1)
        jog_layout.addWidget(self.jog_xm, 1, 0)
        jog_layout.addWidget(self.jog_xp, 1, 2)
        jog_layout.addWidget(self.jog_ym, 2, 1)
        jog_layout.addWidget(self.enter_jog_btn, 3, 0, 1, 3)
        jog_layout.addWidget(self.exit_jog_btn, 4, 0, 1, 3)
        jog_group.setLayout(jog_layout)

        # These are visual indicators only
        for btn in (self.jog_yp, self.jog_xm, self.jog_xp, self.jog_ym):
            btn.setFocusPolicy(Qt.NoFocus)

        actions_group = QGroupBox("Waypoint Actions")
        actions_layout = QVBoxLayout()
        self.capture_btn = QPushButton("Capture Waypoint")
        self.delete_btn = QPushButton("Delete Selected")
        self.clear_btn = QPushButton("Clear All")

        for btn in (self.capture_btn, self.delete_btn, self.clear_btn):
            actions_layout.addWidget(btn)

        actions_group.setLayout(actions_layout)

        controller_img_group = QGroupBox("Controller Layout")
        controller_img_layout = QVBoxLayout()
        controller_img_layout.addWidget(self._controller_image_label())
        controller_img_group.setLayout(controller_img_layout)

        left_panel.addWidget(pose_group)
        left_panel.addWidget(jog_group)
        left_panel.addWidget(actions_group)
        left_panel.addStretch()
        left_panel.addWidget(controller_img_group)

        # Right column: camera stream on top, waypoint table on bottom
        wp_camera_group = QGroupBox("Camera View")
        wp_camera_layout = QVBoxLayout()
        self.wp_camera_label = QLabel("Camera Feed")
        self.wp_camera_label.setAlignment(Qt.AlignCenter)
        self.wp_camera_label.setMinimumSize(320, 240)
        self.wp_camera_label.setStyleSheet(
            "background-color: black; color: white; border: 1px solid black;"
        )
        wp_camera_layout.addWidget(self.wp_camera_label)
        wp_camera_group.setLayout(wp_camera_layout)

        table_group = QGroupBox("Waypoint List")
        table_layout = QVBoxLayout()

        self.wp_table = QTableWidget(0, 3)
        self.wp_table.setHorizontalHeaderLabels(["X", "Y", "Z"])
        self.wp_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.wp_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.wp_table.setEditTriggers(QTableWidget.NoEditTriggers)

        table_layout.addWidget(self.wp_table)
        table_group.setLayout(table_layout)

        right_column = QVBoxLayout()
        right_column.addWidget(wp_camera_group, 1)
        right_column.addWidget(table_group, 1)

        main_layout.addLayout(left_panel, 1)
        main_layout.addLayout(right_column, 2)
        self.waypoints_tab.setLayout(main_layout)
    
    def build_manual_weld_tab(self):
        main_layout = QHBoxLayout()

        left_panel = QVBoxLayout()

        pose_group = QGroupBox("Current Pose")
        pose_layout = QVBoxLayout()

        self.mw_pose_x = QLabel("X: 0.00")
        self.mw_pose_y = QLabel("Y: 0.00")
        self.mw_pose_z = QLabel("Z: 0.00")

        pose_layout.addWidget(self.mw_pose_x)
        pose_layout.addWidget(self.mw_pose_y)
        pose_layout.addWidget(self.mw_pose_z)
        pose_group.setLayout(pose_layout)

        jog_group = QGroupBox("Jog Controls")
        jog_layout = QGridLayout()

        self.mw_jog_yp = QPushButton("Y+")
        self.mw_jog_xm = QPushButton("X-")
        self.mw_jog_xp = QPushButton("X+")
        self.mw_jog_ym = QPushButton("Y-")

        self.enter_manual_weld_btn = QPushButton("Enter Manual Weld Mode")
        self.exit_manual_weld_btn = QPushButton("Exit Manual Weld Mode")

        jog_layout.addWidget(self.mw_jog_yp, 0, 1)
        jog_layout.addWidget(self.mw_jog_xm, 1, 0)
        jog_layout.addWidget(self.mw_jog_xp, 1, 2)
        jog_layout.addWidget(self.mw_jog_ym, 2, 1)
        jog_layout.addWidget(self.enter_manual_weld_btn, 3, 0, 1, 3)
        jog_layout.addWidget(self.exit_manual_weld_btn, 4, 0, 1, 3)

        jog_group.setLayout(jog_layout)

        for btn in (self.mw_jog_yp, self.mw_jog_xm, self.mw_jog_xp, self.mw_jog_ym):
            btn.setFocusPolicy(Qt.NoFocus)

        manual_group = QGroupBox("Manual Weld Instructions")
        manual_layout = QVBoxLayout()

        manual_label = QLabel(
            "Manual Weld Mode\n\n"
            "Use the controller to move normally.\n"
            "A and X do nothing.\n"
            "Right trigger executes a weld at the laser point."
        )

        manual_label.setAlignment(Qt.AlignCenter)   # <-- KEY
        manual_label.setWordWrap(True)

        manual_layout.addWidget(manual_label)
        manual_group.setLayout(manual_layout)

        controller_img_group = QGroupBox("Controller Layout")
        controller_img_layout = QVBoxLayout()
        controller_img_layout.addWidget(self._manual_controller_image_label())
        controller_img_group.setLayout(controller_img_layout)

        left_panel.addWidget(pose_group)
        left_panel.addWidget(jog_group)

        left_panel.addStretch()  # push content toward center

        left_panel.addWidget(manual_group, alignment=Qt.AlignCenter)

        # --- Everything else centered underneath ---
        center_block = QVBoxLayout()

        center_block.addWidget(jog_group, alignment=Qt.AlignCenter)
        center_block.addWidget(controller_img_group, alignment=Qt.AlignCenter)

        left_panel.addLayout(center_block)

        left_panel.addStretch()  # pushes everything slightly upward nicely

        controller_img_group.setMaximumWidth(600)

        camera_group = QGroupBox("Camera View")
        camera_layout = QVBoxLayout()

        self.mw_camera_label = QLabel("Camera Feed")
        self.mw_camera_label.setAlignment(Qt.AlignCenter)
        self.mw_camera_label.setMinimumSize(640, 480)
        self.mw_camera_label.setStyleSheet(
            "background-color: black; color: white; border: 1px solid black;"
        )

        camera_layout.addWidget(self.mw_camera_label)
        camera_group.setLayout(camera_layout)

        main_layout.addLayout(left_panel, 1)
        main_layout.addWidget(camera_group, 2)

        self.manual_weld_tab.setLayout(main_layout)

    # ------------------------------------------------------------------
    # Controller wiring
    # ------------------------------------------------------------------
    def _connect_controller(self):
        c = self.controller

        # Core GUI updates
        c.state_changed.connect(self._on_state_changed)
        c.position_updated.connect(self._on_position_updated)
        c.waypoints_updated.connect(self._refresh_waypoint_table)
        c.progress_updated.connect(self._on_progress_updated)
        c.log_message.connect(self.log_box.append)

        # New hardware/status signals
        c.controller_jog_visual.connect(self._set_jog_button_highlights)
        c.camera_frame_ready.connect(self._update_camera_frame)
        c.laser_state_changed.connect(self._on_laser_state_changed)
        c.force_updated.connect(self._on_force_updated)

        # Operations tab buttons
        self.start_weld_seq_btn.clicked.connect(self._on_start)
        self.estop_btn.clicked.connect(lambda: c.post_event(Event.ESTOP_PRESSED))
        self.home_btn.clicked.connect(c.home)

        # Jog-mode entry/exit only
        self.enter_jog_btn.clicked.connect(lambda: c.post_event(Event.ENTER_JOG_MODE))
        self.exit_jog_btn.clicked.connect(lambda: c.post_event(Event.EXIT_JOG_MODE))

        # Waypoint actions
        self.capture_btn.clicked.connect(lambda: c.post_event(Event.CONFIRM_WELD_POINT))
        self.delete_btn.clicked.connect(self._on_delete_waypoint)
        self.clear_btn.clicked.connect(c.clear_waypoints)

        self.set_travel_height_btn.clicked.connect(self._on_set_travel_height)

        # Manual Weld entry/exit
        self.enter_manual_weld_btn.clicked.connect(
            lambda: c.post_event(Event.ENTER_MANUAL_WELD))
        self.exit_manual_weld_btn.clicked.connect(
            lambda: c.post_event(Event.EXIT_MANUAL_WELD)
)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    def _on_start(self):
        self.controller.prepare_weld_queue()
        self.controller.post_event(Event.START_WELD_SEQUENCE)

    def _on_state_changed(self, state_name: str):
        if state_name == "MANUAL_JOG":
            self.tabs.setCurrentWidget(self.waypoints_tab)

        if state_name == "IDLE" and not self._travel_height_prompted:
            self._travel_height_prompted = True
            QTimer.singleShot(0, self._prompt_travel_height_startup)

        display, mode = _STATE_DISPLAY.get(state_name, (state_name, "—"))
        self.state_label.setText(f"State: {display}")
        self.mode_label.setText(f"Mode: {mode}")

        is_estop = state_name == "EMERGENCY_STOP"
        self.state_label.setStyleSheet(
            "color: red; font-weight: bold;" if is_estop else ""
        )

        self.start_weld_seq_btn.setEnabled(state_name == "IDLE")
        self.home_btn.setEnabled(state_name in ("IDLE", "MANUAL_JOG"))
        self.set_travel_height_btn.setEnabled(state_name in ("IDLE", "MANUAL_JOG"))
        self.enter_jog_btn.setEnabled(state_name == "IDLE")
        self.exit_jog_btn.setEnabled(state_name == "MANUAL_JOG")
        self.capture_btn.setEnabled(state_name in ("MANUAL_JOG", "CAMERA_LASER_TARGETING"))

        if state_name != "MANUAL_JOG":
            self._set_jog_button_highlights(False, False, False, False)
        
        if state_name == "MANUAL_WELD":
            self.tabs.setCurrentWidget(self.manual_weld_tab)

    def _on_position_updated(self, x: float, y: float, z: float):
        self.pose_x_label.setText(f"Pose X: {x:.2f}")
        self.pose_y_label.setText(f"Pose Y: {y:.2f}")
        self.pose_z_label.setText(f"Pose Z: {z:.2f}")

        self.wp_pose_x.setText(f"X: {x:.2f}")
        self.wp_pose_y.setText(f"Y: {y:.2f}")
        self.wp_pose_z.setText(f"Z: {z:.2f}")

        if hasattr(self, "mw_pose_x"):
            self.mw_pose_x.setText(f"X: {x:.2f}")
            self.mw_pose_y.setText(f"Y: {y:.2f}")
            self.mw_pose_z.setText(f"Z: {z:.2f}")

    def _on_progress_updated(self, current: int, total: int):
        self.progress_label.setText(f"Waypoint Progress: {current} / {total}")

    def _on_laser_state_changed(self, is_on: bool):
        self.laser_label.setText(f"Laser: {'ON' if is_on else 'OFF'}")
        self.laser_label.setStyleSheet(
            "color: green; font-weight: bold;" if is_on else ""
        )

    def _on_force_updated(self, value: int):
        self.force_label.setText(f"Force Sensor: {value}")

    # ------------------------------------------------------------------
    # Travel-height dialog
    # ------------------------------------------------------------------
    _TRAVEL_HEIGHT_MIN = 5.0
    _TRAVEL_HEIGHT_MAX = 80.0
    _TRAVEL_HEIGHT_DEFAULT = 25.0

    def _ask_travel_height(self, current: float) -> float:
        dlg = QDialog(self)
        dlg.setWindowTitle("Set Safe Z Travel Height")
        dlg.setModal(True)

        spin = QDoubleSpinBox()
        spin.setRange(self._TRAVEL_HEIGHT_MIN, self._TRAVEL_HEIGHT_MAX)
        spin.setDecimals(1)
        spin.setSingleStep(1.0)
        spin.setSuffix(" mm")
        spin.setValue(current)

        note = QLabel(
            "Enter the height Z raises to before XY moves.\n"
            "Set this 25–50 mm (1–2 inches) above your weld surface.\n"
            f"Range: {self._TRAVEL_HEIGHT_MIN:.0f} – {self._TRAVEL_HEIGHT_MAX:.0f} mm"
        )
        note.setWordWrap(True)

        buttons = QDialogButtonBox()
        ok_btn = buttons.addButton("Set Height", QDialogButtonBox.AcceptRole)
        buttons.addButton("Use Default (25 mm)", QDialogButtonBox.RejectRole)
        ok_btn.setDefault(True)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)

        form = QFormLayout()
        form.addRow("Travel height:", spin)
        layout = QVBoxLayout()
        layout.addWidget(note)
        layout.addLayout(form)
        layout.addWidget(buttons)
        dlg.setLayout(layout)

        return spin.value() if dlg.exec_() == QDialog.Accepted else self._TRAVEL_HEIGHT_DEFAULT

    def _prompt_travel_height_startup(self):
        height = self._ask_travel_height(current=self._TRAVEL_HEIGHT_DEFAULT)
        self.controller.set_travel_height(height)
        self.travel_height_label.setText(f"Travel Z: {height:.1f} mm")

    def _on_set_travel_height(self):
        height = self._ask_travel_height(current=self.controller.travel_height)
        self.controller.set_travel_height(height)
        self.travel_height_label.setText(f"Travel Z: {height:.1f} mm")

    def _refresh_waypoint_table(self, wp_list: list):
        self.wp_table.setRowCount(len(wp_list))
        for row, wp in enumerate(wp_list):
            self.wp_table.setItem(row, 0, QTableWidgetItem(f"{wp['x']:.2f}"))
            self.wp_table.setItem(row, 1, QTableWidgetItem(f"{wp['y']:.2f}"))
            self.wp_table.setItem(row, 2, QTableWidgetItem("0.00"))

    def _on_delete_waypoint(self):
        rows = sorted({item.row() for item in self.wp_table.selectedItems()}, reverse=True)
        for row in rows:
            self.controller.remove_waypoint(row)

    # ------------------------------------------------------------------
    # Visual helpers
    # ------------------------------------------------------------------
    def _controller_image_label(self) -> QLabel:
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignCenter)
        pixmap = QPixmap(_CONTROLLER_IMG)
        if not pixmap.isNull():
            lbl.setPixmap(pixmap.scaled(560, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            lbl.setText("Controller Layout\n(image not found)")
        return lbl
    
    def _manual_controller_image_label(self) -> QLabel:
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignCenter)

        pixmap = QPixmap(_MANUAL_CONTROLLER_IMG)
        if not pixmap.isNull():
            lbl.setPixmap(
                pixmap.scaled(560, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        else:
            lbl.setText("Manual Controller Layout\n(image not found)")

        return lbl

    def _set_jog_button_highlights(self, left=False, right=False, up=False, down=False):
        active_style = "background-color: #90ee90; font-weight: bold;"
        inactive_style = ""

        self.jog_xm.setStyleSheet(active_style if left else inactive_style)
        self.jog_xp.setStyleSheet(active_style if right else inactive_style)
        self.jog_yp.setStyleSheet(active_style if up else inactive_style)
        self.jog_ym.setStyleSheet(active_style if down else inactive_style)

    def _update_camera_frame(self, frame):
        if frame is None:
            return

        try:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
            pixmap = QPixmap.fromImage(qimg)

            self.camera_label.setPixmap(
                pixmap.scaled(self.camera_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            self.wp_camera_label.setPixmap(
                pixmap.scaled(self.wp_camera_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        except Exception as exc:
            log.warning("Failed to update camera frame: %s", exc)

        if hasattr(self, "mw_camera_label"):
            self.mw_camera_label.setPixmap(
                pixmap.scaled(
                    self.mw_camera_label.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    def closeEvent(self, event):
        self.controller.stop()
        super().closeEvent(event)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = QApplication(sys.argv)
    window = SpotWelderGUI()
    window.show()
    sys.exit(app.exec_())