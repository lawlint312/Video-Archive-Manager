<p align="center">
  <img src="frontend/public/brand-lockup.png" alt="Video Archive Manager" width="320">
</p>

<p align="center"><a href="README.md">繁體中文</a> ｜ <b>English</b></p>

# Video Archive Manager (VAM)

VAM is a self-hosted manager for large video and photo collections spread across local drives and a NAS.

It runs in the browser, so it works on a desktop, a phone or a tablet without installing a client on each device.

Tested at:

- 🎬 **30,000+ videos**
- 👤 **730 people**
- 🖼️ **160,000+ photos**
- 💾 spread across 8 local drives and a NAS

> VAM manages your own files. It **contains and provides no media**.
>
> **The interface is in Traditional Chinese only for now.** This page and the screenshots describe what each screen does.

<p align="center"><img src="screenshots/home.jpg" alt="Home: people grouped into categories, each category with its own card shape" width="900"></p>

<sub>Every screenshot on this page comes from a demo library: the people and photos are fictional and AI-generated.</sub>

---

## Core idea

VAM is organised around **people** (角色).

Each person is a folder that can hold:

- videos and photos
- a profile (`person_info.json`, stored next to the files, so moving the folder moves the data)
- social links and tags
- watch history, favourites, titles and release dates

New downloads land in an **inbox** (待分類) first. Once you have checked them, you file them to a person; watch history, titles and the rest move with the files, so nothing has to be redone.

---

# ✨ Features

## 🎬 Browsing and watching

- **Custom categories**: the home page shows people by category, and each category picks its card shape (round, 16:9, 4:5, 9:16) and whether to use cover art.
- **Sorting and filters**: by name, file size, date added or release date; quick filters for **unwatched** and **favourites**.
- **Library-wide search**: find a video by file name or product code; differences in letter case or hyphenation still match.
- **Contact sheets**: frames from across a video gathered into one image, so you know what is in it without playing it through. You can also pick clip ranges straight from the sheet.
- **Cover art**: understands the Kodi / Jellyfin convention of `<name>-poster.jpg` (portrait) and `<name>-fanart.jpg` (landscape), and picks whichever fits the card's shape.
- **Player**: external subtitles (SRT / ASS / VTT, with bilingual subtitles merged automatically), watch history, notes, manual titles, remembered volume and keyboard shortcuts.

<table>
  <tr>
    <td width="50%"><img src="screenshots/character-videos.jpg" alt="A person's videos"></td>
    <td width="50%"><img src="screenshots/contact-sheet.jpg" alt="Contact sheet"></td>
  </tr>
  <tr>
    <td align="center">A person's videos: covers, watched and favourite marks, filters and sorting</td>
    <td align="center">Contact sheet: see what is in a video without watching it</td>
  </tr>
</table>

## 🖼️ Photos

- A photo wall for each person, sorted by date, name, file size or **visual similarity** (composition and colour), with an album view as well.
- Thumbnails are generated and cached by the server; the original only loads when you open a photo.
- **On phones**: swipe up and down between photos, pinch to zoom, and a layout rebuilt for touch.

<table>
  <tr>
    <td width="40%"><img src="screenshots/character-profile.jpg" alt="A person's profile and photo wall"></td>
    <td width="40%"><img src="screenshots/albums.jpg" alt="Albums from subfolders"></td>
    <td width="20%"><img src="screenshots/mobile.jpg" alt="On a phone"></td>
  </tr>
  <tr>
    <td align="center">Profile, social links and photo wall</td>
    <td align="center">Subfolders shown as albums</td>
    <td align="center">On a phone</td>
  </tr>
</table>

---

# 🗂️ Organising and filing

- **Inbox workflow**: preview, watch, then file once you are sure; you can move on to the next video while files are still being moved.
- **File a whole folder**: send an entire folder to a person in one step.
- **Managing people**: rename, merge two people's folders, custom tags (usable as filters), social links, profile details, and framing the avatar.
- **NFO**: reads and writes Kodi / Jellyfin / Emby `.nfo` files. Reading only fills empty fields and never overwrites what you have edited.

<p align="center"><img src="screenshots/inbox.jpg" alt="Inbox: new videos wait here until they are filed" width="800"></p>

---

# 🔍 Duplicate scans

- **Videos**: first by size and hash (identical files), then by duration and frame fingerprints (the same video at a different quality or in another version), with a suggestion of which copy to keep.
- **Images**: finds identical and near-identical photos, including watermarked versions.
- Scans can be stopped part-way and resume by themselves after a server restart; nothing is deleted before you have confirmed a list.

<p align="center"><img src="screenshots/duplicates.jpg" alt="Duplicate scan: an identical file, and the same video at a lower quality" width="800"></p>

---

# 🤖 AI-assisted organising (optional, installed separately)

- **Person detection**: finds photos in a folder with **nobody in them** (landscapes, food, objects) so they are easy to clean up. About 0.07 s per photo on an NVIDIA GPU; it also runs without one, about 5× slower.
- **Face recognition**: analyses faces in inbox videos and suggests who they might be, based on the people you have already set up. Needs an NVIDIA GPU.

If they are not installed, these two features say in the interface which package or model is missing; everything else works as normal. See "AI features" below for installation.

---

# 🎞️ Video tools

Every tool that changes a video **verifies** the result: if verification fails, the original is not deleted.

- **Convert**: for videos a browser cannot play, first tries a **remux without re-encoding**, and only re-encodes when that is not enough.
- **Repair**: rescues fragmented MP4s mislabelled as `.ts`, masks corrupted frames, fixes audio dropouts and interrupted recordings, and corrects frame timing that causes stutter.
- **Full decode check**: decodes every frame once to find files that look fine but are broken in the middle.
- **Shrink**: re-encodes to save space, **always to a new file**. You can play the original and the smaller version side by side, in sync, and compare quality before deciding which to keep.
- **✂️ Clip**: select several segments and join them into one video. Pick rough ranges on the contact sheet, then fine-tune in the player. Choose "fast" (snaps to keyframes, no quality loss) or "frame-accurate" (re-encodes).

<p align="center"><img src="screenshots/player.jpg" alt="Player: edit the title and notes while watching" width="800"></p>

---

# 📚 Filling in metadata

- **Titles and release dates**: looked up by product code in DMM's public catalogue, for the original Japanese title and release date; anything not found there is tried on JAVDatabase.
- **Cover art**: downloads both the portrait front and the landscape sleeve, saved next to the video as `-poster.jpg` / `-fanart.jpg`.
- **Profiles and avatars**: fills in birthday, height and other fields from JAVDatabase; avatar candidates come from the [Gfriends](https://github.com/gfriends/gfriends) collection. Existing values are listed field by field as "current → new", and only the ones you tick are replaced.
- **Import pages you have saved**: Seesaa Wiki, IAFD and AVBase. Parses the list of works, aliases, profile details, and which works were cut from which (compilations, best-ofs).
- **Co-stars**: works out from the data which people have appeared together.

> All sources are public websites. Please follow each site's terms of use. VAM does not bypass any regional restriction or bot protection.

---

# 🔐 Accounts, permissions and security

- **Permission groups**: the administrator creates groups and ticks exactly which features each can use (filing, deleting, duplicate scans, conversion, clipping, metadata lookups…). Permissions are enforced on the server, not just by hiding buttons.
- **Folder access**: each account can be limited to certain library folders.
- **Two-factor authentication**: TOTP (Google Authenticator and similar) plus backup codes; required for any account that can edit. The QR code is generated locally, and the secret never passes through an outside service.
- **Login protection**: three throttling rules ("account + address", "address", "account") with lockouts that grow longer each time; password strength rules; an IP allowlist.
- **Alerts**: the administrator is told about events such as a successful sign-in from an address never used before, or two-factor being turned off, and can see and sign out any device that is signed in.
- **Remote access**: by default only LAN and VPN addresses are accepted. To connect from outside, put Caddy in front for a proper HTTPS certificate (template in `caddy/Caddyfile.example`).
- **Backups**: the databases are backed up every day to another drive of your choice.

<p align="center"><img src="screenshots/permissions.jpg" alt="Permission groups: tick which features each group can use" width="800"></p>

---

# 🚀 Installing and running

VAM is developed and used on **Windows 10 / 11**, and the launch scripts are `.bat` files. The backend is Python and should run on Linux / macOS too, but that has not been tested.

Installation has three layers, and **only the first is required**. A missing optional component never breaks VAM: the feature that needs it says in the interface what is missing.

| Feature | Needs | Layer |
|---|---|---|
| Browsing, playback, filing, managing people, video duplicates, metadata, accounts and permissions | Python, `requirements.txt` | 1. Basic |
| **Clipping, conversion, shrinking, repair, contact sheets, reading video duration** | **FFmpeg** | 1. Basic |
| Web interface (built once) | Node.js | 1. Basic |
| Contact sheet tiling, photo thumbnails, image duplicates, similarity sorting | Pillow | 2. Recommended |
| Frame timing repair | GPAC's `MP4Box` | 2. Optional |
| Face recognition | `requirements-ai.txt`, NVIDIA GPU | 3. AI |
| Person detection | `requirements-ai.txt`, a model file (GPU optional) | 3. AI |

### 1. Basic install

Install three programs first (shown here with Windows' winget; downloading from each official site works too):

```bat
winget install Python.Python.3.11
winget install Gyan.FFmpeg
winget install OpenJS.NodeJS.LTS
```

Then **open a new command prompt** and check that `python --version`, `ffmpeg -version` and `node --version` all answer.
FFmpeg must be on the PATH: clipping, conversion and contact sheets are all done by calling it. With an NVIDIA graphics card, conversion switches to GPU encoding by itself; there is nothing to configure.

Download VAM and install the Python packages:

```bat
git clone https://github.com/lawlint312/Video-Archive-Manager.git
cd Video-Archive-Manager
pip install -r backend/requirements.txt
```

Build the web interface. This is needed only once; running VAM afterwards does not use Node:

```bat
cd frontend
npm install
npm run build
cd ..
```

Before the first start, set the administrator password (if you do not, a random one is generated and written to the log file in `data\logs\`):

```bat
set VAM_ADMIN_PASSWORD=your-password
start.bat
```

Then open **http://localhost:8301/**, sign in as `admin`, and add your library folders and inbox folder under **設定 (Settings) → 資料夾與瀏覽 (Folders and browsing)**.

### 2. Recommended and optional components

**Recommended**: Pillow, which contact sheets, photo thumbnails, image duplicate scans and similarity sorting all need:

```bat
pip install Pillow
```

**Optional**: frame timing repair needs GPAC's MP4Box:

```bat
winget install GPAC.GPAC
```

Restart VAM afterwards (`stop.bat`, then `start.bat`).

### 3. AI features (optional)

These need far more space and hardware than anything else, so check you want them first:

- about **10 GB** of disk (the GPU build of PyTorch alone is 7.5 GB)
- an **NVIDIA graphics card** with a recent driver (the packages use CUDA 12.9); CUDA and cuDNN are installed by pip, nothing separate to install
- on Windows, `insightface` has no prebuilt package and is compiled during installation, so install
  [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) first (select "Desktop development with C++")

```bat
pip install -r backend/requirements-ai.txt
```

**Face recognition**: ready once the packages are installed. The first use downloads insightface's `buffalo_l` model (about 300 MB).
⚠ That model is licensed **for non-commercial research only**; please check [insightface's license terms](https://github.com/deepinsight/insightface#license) before using it.

**Person detection**: also needs a model file. Download `person_yolov8m-seg.pt` (about 55 MB) from
[Bingsu/adetailer](https://huggingface.co/Bingsu/adetailer/blob/main/person_yolov8m-seg.pt)
on Hugging Face and put it at:

```
data\models\person_yolov8m-seg.pt
```

After restarting VAM, a person's photo tab offers "辨識人物" (detect people) and the "沒有人物" (no people) filter.

### Folder layout

```
library root\
├── Person A\                  ← one folder is one person
│   ├── person_info.json      ← profile, tags, links (written when you edit in VAM)
│   ├── video1.mp4
│   ├── video1-poster.jpg     ← optional: cover art
│   └── photo1.jpg
└── Person B\...
```

### Launchers

| File | What it does |
|---|---|
| `start.bat` | Start by hand; only this computer can connect |
| `start-lan.bat` | The same, but open to phones and tablets on the same LAN |
| `start-hidden.vbs` / `start-lan-hidden.vbs` | No window at all; put one in the Startup folder to start with Windows |
| `status.bat` | Whether it is running, where it listens, the latest log lines |
| `stop.bat` | Stop |

More detail in `README-startup.txt` (in Chinese).

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `VAM_ADMIN_USER` / `VAM_ADMIN_PASSWORD` | `admin` / random | The administrator created on first start |
| `VAM_HOST` / `VAM_PORT` | `127.0.0.1` / `8301` | Listening address |
| `VAM_DATA_DIR` | `data\` | Databases and logs |
| `VAM_CACHE_DIR` | `cache\thumbs\` | Thumbnail cache (safe to delete at any time; it grows back) |
| `VAM_SESSION_DAYS` | `7` | How many days a sign-in lasts |

### Tests

Every test file runs on its own. Backend tests are in `backend\tests\`, frontend tests in `frontend\tests\`:

```bat
cd backend
python -X utf8 tests\test_security.py
cd ..\frontend
node tests\mount.test.mjs
```

The tests use only temporary folders and the synthetic files in `testdata\`; they never touch your collection.

---

# 🎯 The problem VAM solves

Once a video and photo collection grows to thousands or tens of thousands of files, the hard part is usually no longer "playing a video" but:

**finding, sorting, identifying, organising, de-duplicating and maintaining the collection over the years.**

VAM brings browsing, managing people, photos, duplicate scans, face recognition, person detection, conversion, shrinking, clipping and metadata imports together in one web application.

> **Managing a large collection stops being tedious file shuffling and becomes an everyday routine you finish in the browser.**

---

## License

Copyright © 2026 lawlint312

Licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0): you may use, modify and distribute it freely; modified versions — including ones run as a network service for others — must publish their source code under the same license.

Developed with the help of Claude Code.
