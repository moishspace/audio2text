import os
import time
import json
import torch
import subprocess
import tempfile
import threading
from PyQt6.QtCore import QThread, pyqtSignal
from audio_utils import convert_m4a_to_wav
from faster_whisper import WhisperModel

class WhisperWorker(QThread):
    progress = pyqtSignal(int)  # Overall progress percentage (0-100)
    step_update = pyqtSignal(int, int)  # Current step, total steps
    status_update = pyqtSignal(str)
    log_message = pyqtSignal(str)  # For sending log messages to the main UI
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    transcript_ready = pyqtSignal(list)

    def __init__(self, file_path, language):
        super().__init__()
        self.file_path = file_path
        self.language = language
        
        # Check if CUDA is available (for NVIDIA GPUs)
        self.has_cuda = torch.cuda.is_available()
        
        # Select device based on availability
        if self.has_cuda:
            self.device = "cuda"
            cuda_device_name = torch.cuda.get_device_name(0)
            self.device_info = f"CUDA GPU: {cuda_device_name}"
            self.speed_multiplier = 0.3  # NVIDIA GPU is very fast with faster-whisper
        else:
            # For M1 Max, use CPU with optimizations
            self.device = "cpu"
            
            # Check if we're on Apple Silicon
            import platform
            if platform.processor() == 'arm':
                self.device_info = "Apple Silicon CPU (M1/M2 optimized)"
                self.speed_multiplier = 0.9  # Updated based on actual performance
            else:
                self.device_info = "CPU"
                self.speed_multiplier = 0.9  # Standard CPU
        
        # Define the main processing steps
        self.steps = [
            "Checking file format",
            "Loading model",
            "Analyzing audio",
            "Transcribing audio",
            "Processing transcript",
            "Saving results"
        ]
        self.total_steps = len(self.steps)
        self.current_step = 0
        
        # Estimate time for transcription (will be calculated based on file duration)
        self.estimated_transcription_time = 0
        self.transcription_start_time = 0
        
        # Optimized settings for faster performance
        self.model_size = "small"  # Use smaller model for faster processing
        self.beam_size = 1  # Smallest beam size for maximum speed
        self.use_vad = True  # Voice activity detection helps with transcription quality
        
        # For longer recordings, enable chunking
        self.chunk_size = 30  # Process in 30-second chunks for better memory efficiency
    
    def update_step(self, step_index, status_text=None):
        """Update current processing step"""
        self.current_step = step_index
        
        # Calculate base progress (each step contributes equally to progress except transcription)
        if step_index < self.total_steps:
            if status_text:
                self.status_update.emit(f"{self.steps[step_index]} - {status_text}")
            else:
                self.status_update.emit(self.steps[step_index])
            
            # Emit current step / total steps
            self.step_update.emit(step_index + 1, self.total_steps)
            
            # Each step contributes equally (except transcription which will be calculated differently)
            if step_index < 3:  # Before transcription
                progress = int((step_index / self.total_steps) * 100)
                self.progress.emit(progress)
                
    def update_transcription_progress(self, percentage):
        """Update progress during the transcription phase"""
        # Transcription (step 3) is weighted heavily in the overall process
        # Steps 0-2 contribute 30%, step 3 (transcription) contributes 50%, steps 4-5
        # contribute the remaining 20%
        
        # Calculate overall progress: 30% from previous steps + (percentage of 50%)
        overall_progress = 30 + int((percentage / 100) * 50)
        self.progress.emit(overall_progress)
            
    def run(self):
        try:
            # Log device information
            self.status_update.emit(f"Using {self.device_info}")
            self.log_message.emit(f"Processing with: {self.device_info}")
            
            # STEP 1: Check and convert file if needed
            self.update_step(0)
            self.log_message.emit(f"Checking file format: {os.path.basename(self.file_path)}")
            if self.file_path.lower().endswith(".m4a"):
                self.update_step(0, "Converting M4A to WAV...")
                self.log_message.emit("File is M4A format, converting to WAV...")
                self.file_path = convert_m4a_to_wav(self.file_path)
                self.log_message.emit(f"Conversion complete: {os.path.basename(self.file_path)}")
            
            # Get audio duration for better progress estimation
            self.log_message.emit("Analyzing audio file duration...")
            duration = self.get_audio_duration(self.file_path)
            # Use the device-specific speed multiplier
            self.estimated_transcription_time = (duration * self.speed_multiplier) + 15  # Add 15 seconds for overhead
            
            self.log_message.emit(f"Audio duration: {self.format_time(duration)}, estimated processing time: {self.format_time(self.estimated_transcription_time)}")
            
            start_time = time.time()
            
            # STEP 2: Load model - Using faster-whisper
            self.update_step(1)
            self.log_message.emit("Loading faster-whisper model...")
            
            # Always use float32 on M1 Mac to avoid the "float16 not supported" error
            compute_type = "float32"
            self.log_message.emit(f"Loading faster-whisper model with compute type: {compute_type}")
            
            # Use appropriate device settings for faster-whisper
            if self.device == "cuda":
                self.log_message.emit("Initializing model on CUDA GPU...")
                model = WhisperModel("medium", device="cuda", compute_type=compute_type)
            else:
                # For both MPS and CPU, use CPU with optimizations
                self.log_message.emit("Initializing model on CPU with optimizations...")
                model = WhisperModel("medium", device="cpu", compute_type=compute_type)
                
            self.log_message.emit("Model successfully loaded")
                
            # STEP 3: Prepare for transcription
            self.update_step(2)
            self.log_message.emit("Analyzing audio for transcription...")
            
            # STEP 4: Transcribe audio (this is the longest step)
            self.update_step(3)
            self.log_message.emit("Starting transcription process...")
            self.transcription_start_time = time.time()
            
            # Start a timer thread to update progress during transcription
            self.keep_updating = True
            self.log_message.emit("Starting progress timer...")
            self.start_progress_timer()
            
            # Start a watchdog timer to prevent infinite hangs
            self.transcription_timeout = False
            
            def watchdog_timer():
                # Set a maximum time limit (3x the estimated time or at least 20 minutes)
                max_wait_time = max(self.estimated_transcription_time * 3, 1200)  # At least 20 minutes
                self.log_message.emit(f"Setting watchdog timer for {self.format_time(max_wait_time)}")
                
                start = self.transcription_start_time
                while time.time() - start < max_wait_time and self.keep_updating:
                    time.sleep(10)  # Check every 10 seconds
                    
                if self.keep_updating:  # If we're still running after the timeout
                    self.log_message.emit("WATCHDOG ALERT: Transcription taking too long, forcing termination")
                    self.transcription_timeout = True
                    self.keep_updating = False
                    
            watchdog = threading.Thread(target=watchdog_timer)
            watchdog.daemon = True
            watchdog.start()
            
            try:
                # Perform the actual transcription with faster-whisper
                self.log_message.emit(f"Beginning transcription of {os.path.basename(self.file_path)} in {self.language} language...")
                language_code = self.language if self.language else "en"
                
                # Log optimized settings being used
                self.log_message.emit(f"Using optimized settings for long recordings: model={self.model_size}, beam_size={self.beam_size}")
                
                # Create new model with smaller size for faster processing
                model = WhisperModel(
                    self.model_size,  # Use small model for faster processing
                    device=self.device,
                    compute_type="float32"
                )
                
                self.log_message.emit(f"Starting transcription with optimized settings...")
                
                # For long recordings, use chunking to avoid memory issues and provide better progress updates
                duration = self.get_audio_duration(self.file_path)
                
                if duration > 60:  # If recording is longer than 5 minutes
                    self.log_message.emit(f"Long recording detected ({self.format_time(duration)}). Using chunked processing...")
                    
                    # Process in smaller chunks for better memory efficiency and faster results
                    segments_list = []
                    
                    # Create a temporary directory for audio chunks
                    with tempfile.TemporaryDirectory() as temp_dir:
                        # Calculate number of chunks
                        chunk_duration = 60  # 60 second chunks
                        num_chunks = int(duration / chunk_duration) + 1
                        
                        self.log_message.emit(f"Processing {num_chunks} chunks of {chunk_duration} seconds each")
                        
                        for i in range(num_chunks):
                            start_chunk_time = i * chunk_duration
                            
                            # Skip processing if we're beyond the audio duration
                            if start_chunk_time >= duration:
                                break
                            
                            # Update progress based on chunks processed
                            chunk_progress = int((i / num_chunks) * 100)
                            chunk_progress = int(((i+1) / num_chunks) * 100)
                            self.update_transcription_progress(chunk_progress)
                            
                            # Extract a chunk of audio
                            chunk_file = os.path.join(temp_dir, f"chunk_{i}.wav")
                            
                            try:
                                # Use ffmpeg to extract a chunk
                                cmd = [
                                    "ffmpeg", "-y",
                                    "-i", self.file_path,
                                    "-ss", str(start_chunk_time),
                                    "-t", str(chunk_duration),
                                    "-c:a", "pcm_s16le",
                                    "-ar", "16000",
                                    chunk_file
                                ]
                                
                                self.log_message.emit(f"Extracting chunk {i+1}/{num_chunks} (at {self.format_time(start_chunk_time)})...")
                                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                                
                                # Transcribe the chunk
                                self.log_message.emit(f"Transcribing chunk {i+1}/{num_chunks}...")
                                result, info = model.transcribe(
                                    chunk_file,
                                    language=language_code,
                                    beam_size=self.beam_size,
                                    vad_filter=self.use_vad,
                                    word_timestamps=False
                                )
                                
                                # Process the segments from this chunk
                                chunk_segments = list(result)
                                
                                # Adjust timestamps to account for chunk position
                                for segment in chunk_segments:
                                    # Adjust the start and end times by adding the chunk start time
                                    segment.start += start_chunk_time
                                    segment.end += start_chunk_time
                                    segments_list.append(segment)
                                
                                # self.log_message.emit(f"Chunk {i+1}/{num_chunks} completed with {len(chunk_segments)} segments")
                                self.log_message.emit(f"Chunk {i+1}/{num_chunks} completed with {len(chunk_segments)} segments ({chunk_progress}% complete)")

                                
                            except Exception as e:
                                self.log_message.emit(f"Error processing chunk {i+1}: {str(e)}")
                                # Continue with next chunk even if this one fails
                        
                        self.log_message.emit(f"Chunked processing complete. Total segments: {len(segments_list)}")
                else:
                    # For shorter recordings, process the entire file at once
                    segments, info = model.transcribe(
                        self.file_path,
                        language=language_code,
                        beam_size=self.beam_size,
                        vad_filter=self.use_vad,
                        word_timestamps=False
                    )
                    
                    # Convert generator to list IMMEDIATELY
                    segments_list = list(segments)
                    self.log_message.emit(f"Transcription complete with {len(segments_list)} segments")
                
                # Log information about the transcription result
                self.log_message.emit(f"Transcription completed successfully!")
                
            except Exception as e:
                self.log_message.emit(f"ERROR during transcription: {str(e)}")
                import traceback
                self.log_message.emit(traceback.format_exc())
                raise  # Re-raise to handle in the outer try/except
             
            finally:
                # Make sure we stop the progress timer even if transcription fails
                self.keep_updating = False
                self.log_message.emit("Stopping progress timer...")
                time.sleep(1.5)  # Give the timer thread time to terminate cleanly
            
            # Complete the transcription progress
            self.update_transcription_progress(100)
            self.log_message.emit("Transcription engine finished processing")
            
            # STEP 5: Process transcript
            self.update_step(4)
            self.log_message.emit("Processing transcript results...")
            structured_transcript = self.process_transcription(segments_list)
            self.log_message.emit(f"Processed {len(structured_transcript)} transcript segments")
            
            # STEP 6: Save results
            self.update_step(5)
            self.log_message.emit(f"Saving transcription to files...")
            txt_file, json_file = self.save_transcription(structured_transcript)
            self.log_message.emit(f"Transcription saved to {os.path.basename(txt_file)} and {os.path.basename(json_file)}")
            
            # Completion - set progress to 100%
            self.progress.emit(100)
            self.status_update.emit("Transcription completed")
            
            # Calculate total processing time
            total_time = time.time() - start_time
            processing_ratio = total_time / duration
            self.log_message.emit(f"Total processing time: {self.format_time(total_time)}")
            self.log_message.emit(f"Processing ratio: {processing_ratio:.2f}x real-time speed")
            
            # Send transcript to UI and signal completion
            self.log_message.emit("Sending transcript to UI...")
            self.transcript_ready.emit(structured_transcript)
            self.finished.emit(f"{txt_file}|{json_file}|{time.time() - start_time:.2f}")
            self.log_message.emit("Transcription process complete!")

        except Exception as e:
            import traceback
            error_msg = f"Error: {str(e)}\n{traceback.format_exc()}"
            self.log_message.emit(f"ERROR: {error_msg}")
            self.error.emit(error_msg)
        
    def start_progress_timer(self):
        """Start a timer to update elapsed time display during transcription"""
        self.keep_updating = True
        
        def update_timer():
            start = self.transcription_start_time
            
            while self.keep_updating:
                try:
                    elapsed = time.time() - start
                    elapsed_str = self.format_time(elapsed)
                    
                    # Only update status text with elapsed time, not progress
                    status_msg = f"Transcribing audio - {elapsed_str} elapsed"
                    self.status_update.emit(status_msg)
                    
                    # Log elapsed time every 30 seconds
                    if int(elapsed) % 30 == 0:
                        self.log_message.emit(f"Still processing - {elapsed_str} elapsed so far")
                        
                except Exception as e:
                    print(f"Error in progress timer: {str(e)}")
                    
                time.sleep(1)  # Update every second
        
        timer_thread = threading.Thread(target=update_timer)
        timer_thread.daemon = True
        timer_thread.start()
        self.log_message.emit("Progress timer started")


    def process_transcription(self, segments):
        """Process the segments from faster-whisper"""
        structured_transcript = []
        self.log_message.emit(f"Processing {len(segments)} transcript segments...")
        
        for i, segment in enumerate(segments):
            # Log progress for longer transcripts
            if i > 0 and i % 100 == 0:
                self.log_message.emit(f"Processed {i}/{len(segments)} segments...")
                
            start_time = segment.start
            end_time = segment.end
            text = segment.text.strip()
            
            timestamp_str = f"({int(start_time//60):02d}:{int(start_time%60):02d})"
            
            structured_transcript.append({
                "start": start_time,
                "end": end_time,
                "text": text,
                "timestamp_str": timestamp_str
            })
        
        self.log_message.emit(f"Transcript processing complete. {len(structured_transcript)} segments created.")
        return structured_transcript

    def save_transcription(self, structured_transcript):
        """Save transcription to files"""
        txt_file = os.path.splitext(self.file_path)[0] + "_transcription.txt"
        json_file = os.path.splitext(self.file_path)[0] + "_transcription.json"

        self.log_message.emit(f"Saving txt transcript to: {os.path.basename(txt_file)}")
        with open(txt_file, "w", encoding="utf-8") as f:
            f.write("\n".join([seg["timestamp_str"] + " " + seg["text"] for seg in structured_transcript]))

        self.log_message.emit(f"Saving json transcript to: {os.path.basename(json_file)}")
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump({"segments": structured_transcript}, f, indent=4, ensure_ascii=False)

        self.log_message.emit(f"Transcription files saved successfully")
        return txt_file, json_file

    def get_audio_duration(self, file_path):
        """Get accurate audio duration using ffprobe"""
        try:
            cmd = [
                "ffprobe", 
                "-v", "error", 
                "-show_entries", "format=duration", 
                "-of", "default=noprint_wrappers=1:nokey=1", 
                file_path
            ]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            duration = float(result.stdout.strip())
            return duration
        except Exception as e:
            # Fallback to an estimate based on file size if ffprobe fails
            try:
                file_size = os.path.getsize(file_path) / (1024 * 1024)  # Size in MB
                # Rough estimation: 1 minute of audio is ~1MB for typical audio files
                estimated_minutes = file_size  
                return estimated_minutes * 60  # Convert to seconds
            except:
                return 60  # Default 1 minute if all else fails

    def format_time(self, seconds):
        """Format seconds as MM:SS or HH:MM:SS for longer durations"""
        if seconds < 3600:
            minutes, seconds = divmod(int(seconds), 60)
            return f"{minutes:02d}:{seconds:02d}"
        else:
            hours, remainder = divmod(int(seconds), 3600)
            minutes, seconds = divmod(remainder, 60)
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"