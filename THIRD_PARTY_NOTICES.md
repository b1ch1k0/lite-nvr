# Third-party software

| Component | License | Use |
|---|---|---|
| [go2rtc](https://github.com/AlexxIT/go2rtc) by Alexey Khit | MIT | streaming engine (binary downloaded at install / bundled in the RPM) |
| `app/static/player/video-rtc.js`, `video-stream.js` | MIT (go2rtc) | browser player, **modified**: larger MSE buffer, adaptive jitter buffer, stall watchdog |
| FastAPI, Starlette, Uvicorn, Jinja2, httpx, python-multipart | MIT / BSD | web panel |
| NumPy | BSD-3-Clause | motion detection |
| FFmpeg | LGPL/GPL (system package) | recording, snapshots, transcoding |
| nginx | BSD-2-Clause (system package) | reverse proxy |
| rclone | MIT (system package, optional) | offload to cloud / SFTP |
