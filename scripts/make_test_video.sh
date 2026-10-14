#!/usr/bin/env bash
# Generate a sync test video: 1080p H.264 CFR, keyframe at frame 0 (and every second),
# big frame counter + timecode so filmed screens can be compared frame by frame.
#   scripts/make_test_video.sh [out.mp4] [fps] [seconds]
set -euo pipefail
OUT="${1:-test.mp4}"
FPS="${2:-25}"
SECS="${3:-60}"

if ffmpeg -hide_banner -filters 2>/dev/null | grep -q ' drawtext '; then
  VF="drawtext=text='%{frame_num}':start_number=0:fontsize=360:fontcolor=white:x=(w-tw)/2:y=(h-th)/2-80,"
  VF+="drawtext=text='%{pts\\:hms}':fontsize=120:fontcolor=yellow:x=(w-tw)/2:y=h-220,"
  # moving bar: one full sweep per second makes sub-second offsets visible
  VF+="drawbox=x='mod(n,${FPS})*w/${FPS}':y=40:w=w/${FPS}:h=80:color=red:t=fill"
  SRC="color=c=0x202020:s=1920x1080:r=${FPS}:d=${SECS}"
else
  echo "ffmpeg has no drawtext (needs libfreetype); falling back to testsrc counter" >&2
  VF="null"
  SRC="testsrc=s=1920x1080:r=${FPS}:d=${SECS}"
fi

ffmpeg -hide_banner -y -f lavfi -i "$SRC" -vf "$VF" \
  -c:v libx264 -profile:v high -level 4.1 -pix_fmt yuv420p -preset medium -crf 20 \
  -g "$FPS" -keyint_min "$FPS" -sc_threshold 0 -r "$FPS" -fps_mode cfr -an \
  -movflags +faststart "$OUT"
echo "wrote $OUT"
