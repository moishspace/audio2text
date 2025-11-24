import os
import subprocess

def convert_m4a_to_wav(input_file):
    output_file = os.path.splitext(input_file)[0] + ".wav"
    try:
        subprocess.run(["ffmpeg", "-y", "-i", input_file, output_file], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return output_file
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to convert M4A to WAV: {str(e)}")