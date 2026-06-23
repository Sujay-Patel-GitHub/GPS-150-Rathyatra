# How the RTMP Live Video Works (Explained)

This document explains how the GPS dashboard takes an **RTMP camera link** and
shows the **live video frames inside a normal web browser**. It is written so a
friend (or any developer) can follow exactly how I built it.

All the code referenced here lives in [`app.py`](app.py) and
[`templates/device_dashboard.html`](templates/device_dashboard.html).

---

## 1. The Core Problem

The vehicle cameras stream video using **RTMP** (Real-Time Messaging Protocol).
An RTMP link looks like this:

```
rtmp://<server-ip>/live/<camera_name>
```

The problem: **web browsers cannot play RTMP directly.** Chrome, Edge, Firefox,
and phones have no built-in RTMP player (RTMP was a Flash-era protocol, and Flash
is dead).

So I needed a "translator" sitting in the middle that:

1. Connects to the RTMP camera,
2. Converts the stream into something the browser *can* play, and
3. Serves it over plain HTTP.

That translator is **FFmpeg**, and the browser-friendly format I convert to is
**HLS** (HTTP Live Streaming).

---

## 2. The Big Picture (Data Flow)

```
 ┌──────────────┐   RTMP    ┌──────────────┐   HLS files   ┌──────────────┐
 │  Vehicle     │ ───────►  │   FFmpeg     │ ───────────►  │ Flask server │
 │  Camera      │           │ (converter)  │  .m3u8 + .ts  │  (app.py)    │
 └──────────────┘           └──────────────┘               └──────┬───────┘
                                                                   │ HTTP
                                                                   ▼
                                                            ┌──────────────┐
                                                            │  Browser     │
                                                            │  (hls.js)    │
                                                            │  plays video │
                                                            └──────────────┘
```

In words:

1. The browser asks my server: *"start playing this RTMP link."*
2. The server launches **FFmpeg**, which connects to the camera over RTMP.
3. FFmpeg chops the live video into small **2-second `.ts` chunks** and keeps an
   updated **`.m3u8` playlist** that lists those chunks.
4. The server hands those files to the browser over normal HTTP.
5. **hls.js** (a JavaScript library) in the browser downloads the chunks in order
   and feeds the video frames into a `<video>` tag — so you see live video.

---

## 3. Where the RTMP Links Are Stored

Each vehicle has up to **4 camera channels** (`rtmp1`, `rtmp2`, `rtmp3`, `rtmp4`).
The links can come from two sources, and the admin chooses which one per vehicle:

- **Firebase** (real-time database), or
- **MongoDB** (`mongo_rtmp` field).

This preference is read in [`app.py`](app.py):

```python
def get_rtmp_source():
    data = col_settings.find_one({"_id": "rtmp_source_pref"})
    if data:
        return data.get("source", "firebase")
    return "firebase"
```

The admin manages these links on the **RTMP Link Management** page
([`templates/manage_rtmp.html`](templates/manage_rtmp.html)), which saves them via
the `/save_rtmp` route. When the dashboard loads, the 4 links are passed into the
page as `streams` so each camera box knows its RTMP URL.

---

## 4. The Heart of It — FFmpeg (RTMP → HLS)

This is the single most important function. It builds the FFmpeg command that does
the actual conversion ([`app.py:360`](app.py)):

```python
def start_ffmpeg(src, out_dir):
    cmd = [
        "ffmpeg", "-fflags", "nobuffer", "-rtbufsize", "1500M",
        "-i", src, "-c:v", "copy", "-c:a", "aac", "-ar", "44100", "-b:a", "128k",
        "-f", "hls", "-hls_time", "2", "-hls_list_size", "1500", "-hls_allow_cache", "0",
        "-hls_flags", "delete_segments+append_list+omit_endlist+independent_segments",
        "-hls_segment_filename", str(out_dir / "segment_%03d.ts"),
        str(out_dir / "index.m3u8"),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc
```

### What each part means

| Flag | What it does |
|------|--------------|
| `-fflags nobuffer` | Don't buffer input — keep latency low (we want *live*). |
| `-rtbufsize 1500M` | Big real-time buffer so a bursty RTMP feed doesn't drop frames. |
| `-i src` | The **input** = the RTMP camera URL. |
| `-c:v copy` | **Copy the video as-is** (no re-encoding). This is the key trick — it's fast and uses almost no CPU because we don't re-compress the picture. |
| `-c:a aac ...` | Re-encode **audio** to AAC, which browsers require. |
| `-f hls` | **Output format = HLS.** |
| `-hls_time 2` | Each chunk is **2 seconds** long. |
| `-hls_list_size 1500` | Keep up to 1500 chunks listed — this gives a long "rewind" (DVR) buffer. |
| `delete_segments` | Auto-delete old chunks so the disk doesn't fill up. |
| `segment_%03d.ts` | The video chunks: `segment_000.ts`, `segment_001.ts`, … |
| `index.m3u8` | The **playlist** file the browser reads first. |

So FFmpeg connects to the camera and continuously writes two kinds of files into a
folder:

- **`index.m3u8`** — a text playlist that always lists the latest chunks.
- **`segment_000.ts`, `segment_001.ts`, …** — the actual 2-second video chunks.

The browser just keeps re-reading `index.m3u8` and downloading whatever new `.ts`
chunks appear. That's how "live" works.

---

## 5. The Server Routes (Flask)

There are four small routes that drive the whole thing.

### a) `/play_rtmp` — start a stream ([`app.py:4591`](app.py))

```python
@app.route("/play_rtmp")
def play_rtmp():
    src = request.args.get("src", "").strip()
    if not src: return jsonify({"error": "No URL"}), 400

    stream_id = str(abs(hash(src)))[:10]          # a short ID derived from the URL
    out_dir = STREAM_ROOT / stream_id
    out_dir.mkdir(exist_ok=True)

    with PROCESS_LOCK:
        LAST_HEARTBEAT[stream_id] = time.time()
        if stream_id not in PROCESS_TABLE or PROCESS_TABLE[stream_id].poll() is not None:
            PROCESS_TABLE[stream_id] = start_ffmpeg(src, out_dir)   # launch FFmpeg

    for _ in range(60):                            # wait up to 30s for first playlist
        if (out_dir / "index.m3u8").exists(): break
        time.sleep(0.5)

    if not (out_dir / "index.m3u8").exists():
        return jsonify({"error": "Stream timeout"}), 500

    return jsonify({"hls_url": f"/hls/{stream_id}/index.m3u8", "stream_id": stream_id})
```

What it does:
- Turns the RTMP URL into a short **`stream_id`** (so the same camera reuses one
  FFmpeg process instead of starting a new one for every viewer).
- Starts FFmpeg if it isn't already running for that camera.
- Waits until the first `index.m3u8` playlist appears.
- Returns the **HLS URL** the browser should play.

### b) `/hls/<stream_id>/<filename>` — serve the video files ([`app.py:4634`](app.py))

```python
@app.route("/hls/<stream_id>/<path:filename>")
def hls(stream_id, filename):
    folder = STREAM_ROOT / stream_id
    if not folder.exists(): abort(404)
    return send_from_directory(folder, filename)
```

This simply hands out the `.m3u8` playlist and `.ts` chunks over HTTP. This is the
"served over plain HTTP" step that makes the video browser-playable.

### c) `/keep_alive` — heartbeat ([`app.py:4623`](app.py))

The browser pings this every few seconds to say *"I'm still watching."* It updates
`LAST_HEARTBEAT` so the server knows the stream is still needed.

### d) `/stop_stream` — stop and clean up ([`app.py:4615`](app.py))

When the user stops a feed (or closes the tab), this kills the FFmpeg process and
deletes the chunk files.

---

## 6. Automatic Cleanup (so the server doesn't choke)

Running FFmpeg processes are expensive. If a browser tab is closed without warning,
we don't want a "zombie" FFmpeg running forever. So a background thread watches
every stream ([`app.py:394`](app.py)):

```python
def cleanup_manager():
    while True:
        now = time.time()
        for sid in list(PROCESS_TABLE.keys()):
            kill_needed = False
            if now - LAST_HEARTBEAT.get(sid, 0) > 15:    # no heartbeat for 15s
                kill_needed = True
            if PROCESS_TABLE[sid].poll() is not None:    # FFmpeg already died
                kill_needed = True
            if kill_needed: kill_stream(sid)
        time.sleep(3)
```

Rule: **if no browser sends a heartbeat for 15 seconds, the stream is killed** and
its files are deleted. This keeps the server clean automatically.

---

## 7. The Browser Side (hls.js)

The browser can't play `.m3u8` natively (except Safari), so I use the **hls.js**
library. It's loaded in the page head:

```html
<!-- HLS.js for video streaming -->
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
```

### Starting a feed ([`device_dashboard.html:1234`](templates/device_dashboard.html))

```javascript
function startStream(uiKey, rtmpUrl) {
    fetch(`/play_rtmp?src=${encodeURIComponent(rtmpUrl)}`)   // ask server to start FFmpeg
        .then(res => res.json())
        .then(data => {
            activeStreams.set(uiKey, data.stream_id);         // remember the stream id
            loadHls(uiKey, data.hls_url + '?t=' + Date.now()); // start playing the HLS url
        });
}
```

### Playing the HLS stream ([`device_dashboard.html:1252`](templates/device_dashboard.html))

```javascript
function loadHls(uiKey, src) {
    const video = document.getElementById(`video-${uiKey}`);
    if (Hls.isSupported()) {
        const hls = new Hls({ startPosition: -1, liveSyncDurationCount: 3, ... });
        hls.loadSource(src);        // point hls.js at our /hls/<id>/index.m3u8
        hls.attachMedia(video);     // bind it to the <video> tag
        hls.on(Hls.Events.MANIFEST_PARSED, () => video.play());  // play once ready
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = src; video.play();   // Safari can play HLS directly
    }
}
```

hls.js does the clever part: it reads the `.m3u8`, downloads the `.ts` chunks in
order, stitches the **video frames** together, and pushes them into the `<video>`
element. The user just sees smooth live video.

### Keeping it alive & cleaning up

```javascript
// Every 3 seconds, tell the server "I'm still watching"
function sendHeartbeat() {
    fetch('/keep_alive', { method: 'POST',
        body: JSON.stringify({ stream_ids: Array.from(activeStreams.values()) }) });
}
setInterval(sendHeartbeat, 3000);

// When the user closes the tab, stop the stream on the server
window.addEventListener("beforeunload", () => {
    activeStreams.forEach(sid => fetch('/stop_stream', { method: 'POST',
        body: JSON.stringify({ stream_id: sid }), keepalive: true }));
});
```

It also handles network/media errors automatically (reconnect, recover) so a brief
camera hiccup doesn't permanently break the feed.

---

## 8. Putting It All Together — One Click, Step by Step

When you click **"Start Feed"** on a camera:

1. **Browser** → calls `/play_rtmp?src=rtmp://...`.
2. **Server** → starts an **FFmpeg** process for that RTMP link.
3. **FFmpeg** → connects to the camera, copies the video, encodes audio to AAC, and
   starts writing `index.m3u8` + 2-second `.ts` chunks into a folder.
4. **Server** → waits for the first playlist, then returns the HLS URL.
5. **Browser (hls.js)** → loads the playlist, downloads chunks, plays the frames in
   the `<video>` tag.
6. **Every 3s** → browser sends a heartbeat so the server keeps FFmpeg running.
7. **On close** → browser tells the server to stop; FFmpeg is killed and files are
   deleted. A cleanup thread also kills any stream with no heartbeat for 15s.

---

## 9. Why I Designed It This Way (Key Decisions)

- **FFmpeg with `-c:v copy`** → near-zero CPU for video, because I don't re-encode
  the picture, only repackage it. This lets one modest server handle many cameras.
- **HLS instead of RTMP in the browser** → works everywhere (Chrome, Edge, Firefox,
  Android, iOS) with no plugins.
- **One FFmpeg per unique URL** → multiple viewers of the same camera share a single
  process (the `stream_id = hash(src)` trick).
- **Heartbeat + auto-cleanup** → no zombie processes, server stays healthy even if a
  browser crashes or loses connection.
- **Long `hls_list_size`** → gives a built-in **DVR / rewind** so the user can scrub
  back in time, not just watch the live edge.

---

### TL;DR

> Browsers can't play RTMP. So when you hit "Start Feed", my Flask server launches
> **FFmpeg**, which connects to the RTMP camera and re-packages the live video into
> **HLS** (a `.m3u8` playlist + small `.ts` chunks) served over HTTP. In the browser,
> **hls.js** downloads those chunks and plays the frames in a `<video>` tag. A
> heartbeat keeps the stream alive, and a cleanup thread shuts down idle streams.
