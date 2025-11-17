import sys
from PyQt6.QtWidgets import QApplication
from ui import WhisperApp

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = WhisperApp()
    window.show()
    sys.exit(app.exec())