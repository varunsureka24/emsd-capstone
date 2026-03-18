import sys
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QTextEdit
)
from PyQt5.QtCore import Qt


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

    def build_operations_tab(self):
        main_layout = QVBoxLayout()
        top_layout = QHBoxLayout()

        # Camera group
        camera_group = QGroupBox("Camera View")
        camera_layout = QVBoxLayout()
        self.camera_label = QLabel("Camera Feed")
        self.camera_label.setAlignment(Qt.AlignCenter)
        self.camera_label.setMinimumSize(600, 400)
        self.camera_label.setStyleSheet("background-color: lightgray; border: 1px solid black;")
        camera_layout.addWidget(self.camera_label)
        camera_group.setLayout(camera_layout)

        # Right-side panel
        right_panel = QVBoxLayout()

        status_group = QGroupBox("Machine Status")
        status_layout = QVBoxLayout()
        status_layout.addWidget(QLabel("State: IDLE"))
        status_layout.addWidget(QLabel("Mode: Manual"))
        status_layout.addWidget(QLabel("Current Area: None"))
        status_layout.addWidget(QLabel("Pose X: 0.00"))
        status_layout.addWidget(QLabel("Pose Y: 0.00"))
        status_layout.addWidget(QLabel("Pose Z: 0.00"))
        status_layout.addWidget(QLabel("Waypoint Progress: 0 / 0"))
        status_group.setLayout(status_layout)

        controls_group = QGroupBox("Controls")
        controls_layout = QGridLayout()
        controls_layout.addWidget(QPushButton("Start"), 0, 0)
        controls_layout.addWidget(QPushButton("Pause"), 0, 1)
        controls_layout.addWidget(QPushButton("Stop"), 1, 0)
        controls_layout.addWidget(QPushButton("E-Stop"), 1, 1)
        controls_layout.addWidget(QPushButton("Home"), 2, 0, 1, 2)
        controls_group.setLayout(controls_layout)

        right_panel.addWidget(status_group)
        right_panel.addWidget(controls_group)
        right_panel.addStretch()

        top_layout.addWidget(camera_group, 2)
        top_layout.addLayout(right_panel, 1)

        # Message log
        log_group = QGroupBox("System Messages")
        log_layout = QVBoxLayout()
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setPlainText("System initialized...\nWaiting for user input...")
        log_layout.addWidget(self.log_box)
        log_group.setLayout(log_layout)

        main_layout.addLayout(top_layout)
        main_layout.addWidget(log_group)
        self.operations_tab.setLayout(main_layout)

    def build_waypoints_tab(self):
        main_layout = QHBoxLayout()

        # Left side controls
        left_panel = QVBoxLayout()

        pose_group = QGroupBox("Current Pose")
        pose_layout = QVBoxLayout()
        pose_layout.addWidget(QLabel("X: 0.00"))
        pose_layout.addWidget(QLabel("Y: 0.00"))
        pose_layout.addWidget(QLabel("Z: 0.00"))
        pose_group.setLayout(pose_layout)

        jog_group = QGroupBox("Jog Controls")
        jog_layout = QGridLayout()
        jog_layout.addWidget(QPushButton("Y+"), 0, 1)
        jog_layout.addWidget(QPushButton("X-"), 1, 0)
        jog_layout.addWidget(QPushButton("X+"), 1, 2)
        jog_layout.addWidget(QPushButton("Y-"), 2, 1)
        jog_layout.addWidget(QPushButton("Z+"), 3, 1)
        jog_layout.addWidget(QPushButton("Z-"), 4, 1)
        jog_group.setLayout(jog_layout)

        actions_group = QGroupBox("Waypoint Actions")
        actions_layout = QVBoxLayout()
        actions_layout.addWidget(QPushButton("Capture Waypoint"))
        actions_layout.addWidget(QPushButton("Delete Selected"))
        actions_layout.addWidget(QPushButton("Clear All"))
        actions_layout.addWidget(QPushButton("Move Up"))
        actions_layout.addWidget(QPushButton("Move Down"))
        actions_group.setLayout(actions_layout)

        left_panel.addWidget(pose_group)
        left_panel.addWidget(jog_group)
        left_panel.addWidget(actions_group)
        left_panel.addStretch()

        # Right side table
        table_group = QGroupBox("Waypoint List")
        table_layout = QVBoxLayout()
        self.wp_table = QTableWidget(4, 4)
        self.wp_table.setHorizontalHeaderLabels(["Index", "X", "Y", "Z"])

        sample_data = [
            ["1", "25.00", "40.00", "0.00"],
            ["2", "30.00", "42.50", "0.00"],
            ["3", "35.00", "45.00", "0.00"],
            ["4", "40.00", "47.50", "0.00"],
        ]

        for row, data in enumerate(sample_data):
            for col, value in enumerate(data):
                self.wp_table.setItem(row, col, QTableWidgetItem(value))

        table_layout.addWidget(self.wp_table)
        table_group.setLayout(table_layout)

        main_layout.addLayout(left_panel, 1)
        main_layout.addWidget(table_group, 2)

        self.waypoints_tab.setLayout(main_layout)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SpotWelderGUI()
    window.show()
    sys.exit(app.exec_())