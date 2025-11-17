import os
import json
import subprocess
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QFileDialog, QTextBrowser, QTextEdit,
    QLabel, QProgressBar, QMessageBox, QComboBox, QSizePolicy
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtCore import Qt, QUrl, QSize, pyqtSignal
from PyQt6.QtGui import QFont, QIcon, QTextCursor, QTextCharFormat, QColor
from transcriber import WhisperWorker

class TimestampTextBrowser(QTextBrowser):
    timestamp_clicked = pyqtSignal(float)  # Signal with seconds as parameter
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenLinks(False)  # Prevent automatic link handling
        self.anchorClicked.connect(self.handle_anchor_click)
        
        # Set some styling to make it more readable
        self.setStyleSheet("""
            QTextBrowser {
                background-color: white;
                font-family: Arial, sans-serif;
                font-size: 12pt;
                line-height: 1.5;
            }
        """)
    
    def handle_anchor_click(self, url):
        # Extract timestamp from URL fragment
        fragment = url.fragment()
        if fragment.startswith('time_'):
            try:
                seconds = float(fragment[5:])  # Extract seconds from "time_123.45"
                self.timestamp_clicked.emit(seconds)
            except ValueError:
                pass

class WhisperApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WhisperX Meeting Transcriber")
        self.setGeometry(300, 300, 800, 600)
        self.selected_file = ""
        self.transcribed_file = ""

        self.initUI()

    def initUI(self):
        main_layout = QVBoxLayout()
        main_layout.setSpacing(10)

        # Top controls section (Language Selection & File Buttons)
        top_controls = QHBoxLayout()

        # Language selection
        language_layout = QVBoxLayout()
        language_label = QLabel("Language:")
        language_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))

        self.language_combo = QComboBox()
        self.language_combo.addItems(["English", "Spanish", "French", "German", "Hebrew", "Chinese", "Arabic"])
        self.language_combo.setMaximumWidth(150)
        self.language_map = {
            "English": "en",
            "Spanish": "es",
            "French": "fr",
            "German": "de",
            "Hebrew": "he",
            "Chinese": "zh",
            "Arabic": "ar"
        }

        language_layout.addWidget(language_label)
        language_layout.addWidget(self.language_combo)

        # File buttons
        file_buttons_layout = QHBoxLayout()
        file_buttons_layout.setSpacing(10)

        self.select_button = QPushButton("Select Recording")
        self.select_button.setIcon(QIcon.fromTheme("document-open"))
        self.select_button.setMinimumSize(QSize(150, 50))
        self.select_button.clicked.connect(self.select_file)

        self.load_transcription_button = QPushButton("Load Existing")
        self.load_transcription_button.setIcon(QIcon.fromTheme("document-open-recent"))
        self.load_transcription_button.setMinimumSize(QSize(150, 50))
        self.load_transcription_button.clicked.connect(self.load_transcription)

        self.transcribe_button = QPushButton("Start Transcription")
        self.transcribe_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                border-radius: 4px;
                border: none;
                padding: 8px 16px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #45a049;
                box-shadow: 0 2px 5px rgba(0, 0, 0, 0.2);
            }
            QPushButton:pressed {
                background-color: #3d8b40;
            }
            QPushButton:disabled {
                background-color: #cccccc;
                color: #888888;
            }
        """)
        self.transcribe_button.setMinimumSize(QSize(150, 50))
        self.transcribe_button.setEnabled(False)
        self.transcribe_button.clicked.connect(self.start_transcription)

        file_buttons_layout.addWidget(self.select_button)
        file_buttons_layout.addWidget(self.load_transcription_button)
        file_buttons_layout.addWidget(self.transcribe_button)

        top_controls.addLayout(language_layout)
        top_controls.addStretch()
        top_controls.addLayout(file_buttons_layout)

        # Progress section
        progress_layout = QVBoxLayout()
        
        # Add step counter label
        self.step_label = QLabel("")
        self.step_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_bar.setFormat("%p%")

        progress_layout.addWidget(self.step_label)
        progress_layout.addWidget(self.status_label)
        progress_layout.addWidget(self.progress_bar)

        # Media Player Controls
        player_controls = QHBoxLayout()
        player_controls.setSpacing(10)
        player_controls.setContentsMargins(0, 5, 0, 5)  # Add some vertical padding

        # Create a container widget for better alignment
        player_container = QWidget()
        player_container.setFixedHeight(50)
        player_layout = QHBoxLayout(player_container)
        player_layout.setContentsMargins(0, 0, 0, 0)
        player_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Setup media player
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.media_player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(1.0)

        # Position label (left side)
        self.position_label_left = QLabel("00:00")
        self.position_label_left.setStyleSheet("color: #B3B3B3; font-size: 11px;")  # Spotify gray
        self.position_label_left.setFixedWidth(40)
        self.position_label_left.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # Play button (center)
        self.play_button = QPushButton("▶")
        self.play_button.setFixedSize(QSize(40, 40))
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(self.toggle_playback)
        # Spotify-like styling
        self.play_button.setStyleSheet("""
            QPushButton {
                background-color: #1DB954;  /* Spotify green */
                color: white;
                font-size: 16px;
                font-weight: bold;
                border-radius: 20px;  /* Half of width/height for circle */
                border: none;
            }
            QPushButton:hover {
                background-color: #1ED760;  /* Slightly lighter green on hover */
            }
            QPushButton:pressed {
                background-color: #1AA34A;  /* Slightly darker green when pressed */
            }
            QPushButton:disabled {
                background-color: #535353;  /* Gray when disabled */
                color: #B3B3B3;
            }
        """)

        # Duration label (right side)
        self.position_label_right = QLabel("00:00")
        self.position_label_right.setStyleSheet("color: #B3B3B3; font-size: 11px;")  # Spotify gray
        self.position_label_right.setFixedWidth(40)
        self.position_label_right.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # Add widgets to player layout with proper spacing
        player_layout.addStretch(1)  # Push everything to center
        player_layout.addWidget(self.position_label_left)
        player_layout.addWidget(self.play_button)
        player_layout.addWidget(self.position_label_right)
        player_layout.addStretch(1)  # Push everything to center

        # Add the player container to the main player controls
        player_controls.addWidget(player_container)

        # Transcript Display
        # self.transcript_text = QTextBrowser()
        # self.transcript_text.setHtml("<p>Transcript will appear here after processing...</p>")
        # self.transcript_text.setFont(QFont("Arial", 11))
        
        self.transcript_text = TimestampTextBrowser()
        self.transcript_text.setHtml("<p>Transcript will appear here after processing...</p>")
        self.transcript_text.setFont(QFont("Arial", 11))
        self.transcript_text.timestamp_clicked.connect(self.jump_to_timestamp)

        # Log section
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(80)

        # Open file button
        self.open_button = QPushButton("Open Transcription File")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_file)

        bottom_controls = QHBoxLayout()
        bottom_controls.addStretch()
        bottom_controls.addWidget(self.open_button)

        # Add all sections to the layout
        main_layout.addLayout(top_controls)
        main_layout.addLayout(progress_layout)
        main_layout.addLayout(player_controls)
        main_layout.addWidget(QLabel("Transcript:"))
        main_layout.addWidget(self.transcript_text, 1)
        main_layout.addWidget(QLabel("Log:"))
        main_layout.addWidget(self.log_text)
        main_layout.addLayout(bottom_controls)

        self.setLayout(main_layout)

    def select_file(self):
        file_dialog = QFileDialog()
        file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        file_dialog.setNameFilter("Audio/Video Files (*.mp3 *.wav *.m4a *.mp4);;All Files (*)")
        
        # Execute the dialog directly without using parent widget
        if file_dialog.exec():
            selected_files = file_dialog.selectedFiles()
            if selected_files:
                file_path = selected_files[0]
                self.selected_file = file_path
                self.log_text.append(f"Selected file: {file_path}")
                self.transcribe_button.setEnabled(True)

                self.media_player.setSource(QUrl.fromLocalFile(file_path))
                self.play_button.setEnabled(True)
        else:
            self.log_text.append("No file selected")
    
    def log_message(self, message):
        """Append a message to the log text widget and ensure it's visible"""
        # Get current time for timestamp
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Format the log message with timestamp
        formatted_message = f"[{timestamp}] {message}"
        
        # Append to log widget
        self.log_text.append(formatted_message)
        
        # Scroll to the bottom to ensure the latest message is visible
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())
        
        # Process events to update the UI immediately
        from PyQt6.QtCore import QCoreApplication
        QCoreApplication.processEvents()
        
        # Also print to console for debugging
        print(formatted_message)

                          
    def start_transcription(self):
        if not self.selected_file:
            QMessageBox.warning(self, "Warning", "Please select a file first.")
            return

        selected_language = self.language_combo.currentText()
        language_code = self.language_map[selected_language]

        self.transcribe_button.setEnabled(False)
        self.progress_bar.setValue(0)
        self.step_label.setText("Preparing...")
        self.status_label.setText("Starting transcription...")
        self.log_text.append(f"Starting transcription in {selected_language}...")

        self.worker = WhisperWorker(self.selected_file, language_code)
        self.worker.progress.connect(self.update_progress)
        self.worker.step_update.connect(self.update_step)
        self.worker.status_update.connect(self.update_status)
        self.worker.log_message.connect(self.log_message)  # Connect the log signal
        self.worker.finished.connect(self.transcription_done)
        self.worker.error.connect(self.handle_error)
        self.worker.transcript_ready.connect(self.display_transcript)
        self.worker.start()

    def update_progress(self, value):
        self.progress_bar.setValue(value)

    def update_step(self, current_step, total_steps):
        self.step_label.setText(f"Step {current_step} of {total_steps}")

    def update_status(self, status_text):
        self.status_label.setText(status_text)


    def transcription_done(self, output_data):
        txt_file, json_file, _ = output_data.split("|")
        self.transcribed_file = txt_file
        self.open_button.setEnabled(True)
        self.log_text.append(f"Transcription saved: {txt_file}")

    def load_audio_file(self, file_path):
        """Load an audio file into the media player"""
        self.selected_file = file_path
        self.media_player.setSource(QUrl.fromLocalFile(file_path))
        self.play_button.setEnabled(True)
        
        # Make sure position tracking is set up
        if not hasattr(self, 'position_timer_setup'):
            self.media_player.positionChanged.connect(self.update_position_display)
            self.media_player.durationChanged.connect(self.update_duration)
            self.position_timer_setup = True
            
    def load_transcription(self):
        file_dialog = QFileDialog()
        json_path, _ = file_dialog.getOpenFileName(self, "Select Transcription File", "", "JSON Files (*.json)")
        if not json_path:
            return
        try:
            # Load and parse the JSON file
            with open(json_path, "r", encoding="utf-8") as f:
                transcript_data = json.load(f)
            
            # Display the transcript
            self.display_transcript(transcript_data.get("segments", []))
            
            # Try to find the corresponding audio file
            audio_base = os.path.splitext(json_path)[0]
            possible_extensions = [".mp3", ".wav", ".m4a", ".mp4"]
            
            # First try to find the original audio file by removing "_transcription" suffix
            if "_transcription" in audio_base:
                original_base = audio_base.replace("_transcription", "")
                for ext in possible_extensions:
                    audio_path = original_base + ext
                    if os.path.exists(audio_path):
                        # Found the audio file!
                        self.load_audio_file(audio_path)
                        self.log_text.append(f"Loaded audio file: {audio_path}")
                        return
            
            # If we didn't find it that way, try all possible extensions for the base name
            for ext in possible_extensions:
                audio_path = audio_base + ext
                if os.path.exists(audio_path):
                    # Found the audio file!
                    self.load_audio_file(audio_path)
                    self.log_text.append(f"Loaded audio file: {audio_path}")
                    return
            
            # If we get here, we couldn't find the audio file
            self.log_text.append("Could not find corresponding audio file. Please select it manually.")
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load transcription: {str(e)}")

    def load_transcription1(self):
        file_dialog = QFileDialog()
        json_path, _ = file_dialog.getOpenFileName(self, "Select Transcription File", "", "JSON Files (*.json)")
        if not json_path:
            return
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                transcript_data = json.load(f)
            self.display_transcript(transcript_data.get("segments", []))
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load transcription: {str(e)}")

    def handle_error(self, error_msg):
        self.log_text.append(f"Error: {error_msg}")
        self.transcribe_button.setEnabled(True)

    def display_transcript(self, segments):
        html_content = """
        <style>
            a.timestamp { 
                color: #007bff; 
                text-decoration: none; 
                font-weight: bold;
                background-color: #e9f5ff;
                padding: 2px 4px;
                border-radius: 3px;
            }
            a.timestamp:hover { 
                text-decoration: underline; 
                background-color: #cce5ff;
            }
            p {
                margin: 8px 0;
            }
        </style>
        """
        
        for seg in segments:
            start_time = seg['start']
            timestamp_str = seg['timestamp_str']
            text = seg["text"]
            
            # Create link with time in the fragment
            html_content += f'<p><a href="#time_{start_time}" class="timestamp">{timestamp_str}</a> {text}</p>'
        
        # Set the HTML content
        self.transcript_text.setHtml(html_content)

    def toggle_playback(self):
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
            self.play_button.setText("▶")
        else:
            self.media_player.play()
            self.play_button.setText("⏸")
        
        # Make sure position tracking is set up
        if not hasattr(self, 'position_timer_setup'):
            self.media_player.positionChanged.connect(self.update_position_display)
            self.media_player.durationChanged.connect(self.update_duration)
            self.position_timer_setup = True

    def update_duration(self, duration):
        """Update the duration display when media is loaded"""
        if duration > 0:
            # Convert milliseconds to MM:SS format
            duration_seconds = duration // 1000
            minutes, seconds = divmod(duration_seconds, 60)
            duration_str = f"{minutes:02d}:{seconds:02d}"
            
            # Update right position label (duration)
            self.position_label_right.setText(duration_str)
            
            # Also update left position label (current position)
            position_ms = self.media_player.position()
            position_seconds = position_ms // 1000
            minutes, seconds = divmod(position_seconds, 60)
            position_str = f"{minutes:02d}:{seconds:02d}"
            
            # Update left position label
            self.position_label_left.setText(position_str)

    def open_file(self):
        if self.transcribed_file:
            subprocess.run(["open", self.transcribed_file])
            
      
    def jump_to_timestamp(self, seconds):
        """Jump to a specific timestamp in the audio playback"""
        # Convert seconds to milliseconds for media player
        position_ms = int(seconds * 1000)
        
        # Set the position in the media player
        self.media_player.setPosition(position_ms)
        
        # Update the position display immediately
        self.update_position_display(position_ms)
        
        # Start playback from this position
        self.media_player.play()
        self.play_button.setText("⏸")
        
        # Make sure position updates are connected
        if not hasattr(self, 'position_timer_setup'):
            self.media_player.positionChanged.connect(self.update_position_display)
            self.media_player.durationChanged.connect(self.update_duration)
            self.position_timer_setup = True
        
    def update_position_display(self, position_ms):
        """Update the position display with current playback position"""
        if self.media_player.duration() > 0:
            # Convert milliseconds to MM:SS format
            position_seconds = position_ms // 1000
            minutes, seconds = divmod(position_seconds, 60)
            position_str = f"{minutes:02d}:{seconds:02d}"
            
            # Update left position label
            self.position_label_left.setText(position_str)
            
            # Get duration for right label
            duration_ms = self.media_player.duration()
            duration_seconds = duration_ms // 1000
            minutes, seconds = divmod(duration_seconds, 60)
            duration_str = f"{minutes:02d}:{seconds:02d}"
            
            # Update right position label
            self.position_label_right.setText(duration_str)