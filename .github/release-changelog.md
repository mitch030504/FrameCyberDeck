**Sideloader-first**

- VR CyberDeck now runs as a pure sideloader out of the box — no server, no account, no setup, nothing phoning home. Launch it, plug in your headset, and go.
- The game list has been replaced by a drag-and-drop install deck front and center over the new cyberdeck background. Drop an APK, a `.zip`, a game folder (APK + OBB + `install.txt`), or an OBB folder named after its package, and it deploys to the headset.
- Beveled, non-rectangular sci-fi controls for Install APK, Install Folder, Copy OBB, ADB Shell, Installs, Manage Remotes, and Other Settings.
- Drops are routed automatically: APKs / ZIPs / game folders install, and a folder named after a package is copied in as OBB data.

**Optional server**

- Prefer a browsable library? You can add your own server under **Manage Remotes** — either a server config or your own rclone config. Add one and the full library view (search, sort, card/table, update badges) comes right back. Don't have one? Just use it as a sideloader — that's the whole point.
- The old always-on bundled server has been removed. A server is now entirely opt-in: either you know one, or you don't need one.

**Meta owned-title sync**

- Sign in to your Meta account from the uploads view (in-app browser login with 2FA, or paste a user token). The token is stored encrypted through the OS keychain; passwords never touch the app.
- "Sync Meta Library" compares your owned Quest titles against the server catalog: updates for anything the server already carries, plus new titles you actually paid for (purchases, preorders, cross-buy, and Horizon+ subscription claims — free downloads stay off the server).
- Selected titles download their APK and OBB files from Meta's entitled CDN and go through the regular contribution pipeline. Downloaded versions are cached and never re-fetched.

**Steam Frame fixes**

- Raw-bundle (non-OBB) game data — Unity asset-bundle releases such as Blade & Sorcery Nomad — now installs with its full payload instead of an APK-only install.
- Conversions now apply [FramePort](https://github.com/spoopyghosty0/frameport)'s tested per-game recipes by spoopyghosty0: engine-bug fixes (pose timing, controller vibration freezes, GPU-hang shader fixes), alternate OVRPort patch sets, and the matching shim libraries — all credited to the FramePort project (GPLv3).

**Cleanup**

- Removed the donation banner and the extra community links.
- GitHub link now points to the current repository.
