"""Check a rendered film: streams, duration, black runs, loudness/peak, and pull a few frames for a look.

Usage: python qa.py ../output/watchtower-pitch-v1.mp4 [t1 t2 ...]
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

FFMPEG = shutil.which("ffmpeg") or str(next(Path.home().glob(
    "AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg*/*/bin/ffmpeg.exe")))
FFPROBE = str(Path(FFMPEG).with_name("ffprobe" + Path(FFMPEG).suffix))
video = Path(sys.argv[1]).resolve()
times = [float(t) for t in sys.argv[2:]] or [2, 30, 47, 70, 100, 126]

probe = json.loads(subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
                                  capture_output=True, text=True, check=True).stdout)
for s in probe["streams"]:
    if s["codec_type"] == "video":
        print(f"video  {s['codec_name']} {s['width']}x{s['height']} {s.get('r_frame_rate')} {float(s.get('duration', 0)):.2f}s")
    else:
        print(f"audio  {s['codec_name']} {s.get('sample_rate')} Hz {s.get('channels')} ch {float(s.get('duration', 0)):.2f}s")
fmt = probe["format"]
print(f"file   {float(fmt['duration']):.2f}s  {int(fmt['size']) / 1e6:.1f} MB  {int(fmt.get('bit_rate', 0)) / 1e6:.1f} Mb/s")

black = subprocess.run([FFMPEG, "-v", "info", "-i", str(video), "-vf", "scale=192:-2,blackdetect=d=0.3:pix_th=0.08", "-an",
                        "-f", "null", "-"], capture_output=True, text=True).stderr
runs = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", black)
print("black runs:", runs or "none")

loud = subprocess.run([FFMPEG, "-v", "info", "-i", str(video), "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"],
                      capture_output=True, text=True).stderr
summary = loud[loud.rfind("Summary:"):]
i = re.search(r"I:\s+(-?[\d.]+) LUFS", summary)
tp = re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary)
print(f"loudness {i.group(1) if i else '?'} LUFS integrated, true peak {tp.group(1) if tp else '?'} dBFS")

out = video.with_suffix("")
out = out.parent / f"{out.name}-frames"
out.mkdir(exist_ok=True)
for t in times:
    subprocess.run([FFMPEG, "-y", "-v", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1", "-vf", "scale=960:-2",
                    str(out / f"t{t:06.1f}.jpg")], check=True)
print("frames ->", out)
