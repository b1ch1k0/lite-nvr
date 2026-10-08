import os

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("NVR_DATA", "/var/lib/nvr")
REC_DIR = os.environ.get("NVR_REC", "/srv/nvr/rec")
DB_PATH = os.path.join(DATA, "nvr.db")
GO2RTC_API = os.environ.get("NVR_GO2RTC_API", "http://127.0.0.1:1984")
GO2RTC_RTSP = os.environ.get("NVR_GO2RTC_RTSP", "rtsp://127.0.0.1:8554")
GO2RTC_YAML = os.path.join(DATA, "go2rtc.yaml")
RCLONE_CONF = os.path.join(DATA, "rclone.conf")
EVENTS_DIR = os.environ.get("NVR_EVENTS", "/srv/nvr/events")
POSTER_DIR = os.environ.get("NVR_POSTERS", "/srv/nvr/posters")
GCS_KEY = os.path.join(DATA, "gcs-sa.json")
SFTP_KEY = os.path.join(DATA, "sftp_ed25519")
