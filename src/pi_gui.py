import sys
import logging
import cv2

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QTextEdit
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap

from weld_controller import WeldController
from state_machine import Event

log = logging.getLogger(__name__)

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

        self.tabs.addTab(self.operations_tab, "Operations")
        self.tabs.addTab(self.waypoints_tab, "Waypoints")

        self.build_operations_tab()
        self.build_waypoints_tab()

        self.controller = WeldController(
            enable_grbl=False,
            enable_force_sensor=False,
            enable_controller=True,
            enable_camera=False,
            enable_laser=False,
            enable_weld_relay=False,
        )
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

        for lbl in (
            self.state_label,
            self.mode_label,
            self.pose_x_label,
            self.pose_y_label,
            self.pose_z_label,
            self.progress_label,
            self.laser_label,
            self.force_label,
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
        controls_group.setLayout(controls_layout)

        self.estop_btn.setStyleSheet(
            "background-color: red; color: white; font-weight: bold;"
        )
        self.pause_btn.setEnabled(False)

        right_panel.addWidget(status_group)
        right_panel.addWidget(controls_group)
        right_panel.addStretch()

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

        left_panel.addWidget(pose_group)
        left_panel.addWidget(jog_group)
        left_panel.addWidget(actions_group)
        left_panel.addStretch()

        table_group = QGroupBox("Waypoint List")
        table_layout = QVBoxLayout()

        self.wp_table = QTableWidget(0, 3)
        self.wp_table.setHorizontalHeaderLabels(["X", "Y", "Z"])
        self.wp_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.wp_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.wp_table.setEditTriggers(QTableWidget.NoEditTriggers)

        table_layout.addWidget(self.wp_table)
        table_group.setLayout(table_layout)

        main_layout.addLayout(left_panel, 1)
        main_layout.addWidget(table_group, 2)
        self.waypoints_tab.setLayout(main_layout)

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

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    def _on_start(self):
        self.controller.prepare_weld_queue()
        self.controller.post_event(Event.START_WELD_SEQUENCE)

    def _on_state_changed(self, state_name: str):
        display, mode = _STATE_DISPLAY.get(state_name, (state_name, "—"))
        self.state_label.setText(f"State: {display}")
        self.mode_label.setText(f"Mode: {mode}")

        is_estop = state_name == "EMERGENCY_STOP"
        self.state_label.setStyleSheet(
            "color: red; font-weight: bold;" if is_estop else ""
        )

        self.start_weld_seq_btn.setEnabled(state_name == "IDLE")
        self.home_btn.setEnabled(state_name in ("IDLE", "MANUAL_JOG"))
        self.enter_jog_btn.setEnabled(state_name == "IDLE")
        self.exit_jog_btn.setEnabled(state_name == "MANUAL_JOG")
        self.capture_btn.setEnabled(state_name in ("MANUAL_JOG", "CAMERA_LASER_TARGETING"))

        if state_name != "MANUAL_JOG":
            self._set_jog_button_highlights(False, False, False, False)

    def _on_position_updated(self, x: float, y: float, z: float):
        self.pose_x_label.setText(f"Pose X: {x:.2f}")
        self.pose_y_label.setText(f"Pose Y: {y:.2f}")
        self.pose_z_label.setText(f"Pose Z: {z:.2f}")

        self.wp_pose_x.setText(f"X: {x:.2f}")
        self.wp_pose_y.setText(f"Y: {y:.2f}")
        self.wp_pose_z.setText(f"Z: {z:.2f}")

    def _on_progress_updated(self, current: int, total: int):
        self.progress_label.setText(f"Waypoint Progress: {current} / {total}")

    def _on_laser_state_changed(self, is_on: bool):
        self.laser_label.setText(f"Laser: {'ON' if is_on else 'OFF'}")
        self.laser_label.setStyleSheet(
            "color: green; font-weight: bold;" if is_on else ""
        )

    def _on_force_updated(self, value: int):
        self.force_label.setText(f"Force Sensor: {value}")

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
            bytes_per_line = ch * w
            qimg = QImage(
                rgb.data, w, h, bytes_per_line, QImage.Format_RGB888
            )
            pixmap = QPixmap.fromImage(qimg)

            self.camera_label.setPixmap(
                pixmap.scaled(
                    self.camera_label.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
            )
        except Exception as exc:
            log.warning("Failed to update camera frame: %s", exc)

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