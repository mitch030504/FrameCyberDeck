# Room scan

Frame CyberDeck can start a mixed-reality room capture on a connected Steam
Frame through FramePort's on-Frame agent. The capture runs on the Frame; Frame
CyberDeck only starts it, polls status, and stops it.

This is **capture only**. Captured rooms are not copied into individual MR game
installs in this release.

## Requirements

- A Steam Frame connected and selected in Frame CyberDeck.
- SteamOS Devkit access, because the agent is uploaded and run over the existing
  Devkit SSH connection.
- The vendored FramePort agent, which Frame CyberDeck uploads automatically when
  it is missing or older than the bundled copy.

The agent is installed on the Frame at:

```text
~/.local/share/frame-cyberdeck/agent/frameport_agent.py
```

Frame CyberDeck does not replace a newer agent that another FramePort install
put on the same Frame.

## Scan flow

1. Connect and select the Steam Frame.
2. Open **Other Settings → Steam Frame Conversion → Mixed Reality Room**.
3. Choose **Scan room on Frame**.
4. Put on the headset and follow the on-device scan prompts.
5. Watch the state in the panel: `waiting`, `recording`, `building`, or the
   reported error.
6. Choose **Cancel scan** to stop an active capture. The stop command is also
   sent when the panel reports an active capture and you cancel it.

The capture request uses a 90-second recording window and allows up to 180
seconds for the on-Frame build. Actual timing depends on the room and the
Frame's state.

The captured room remains on the Frame. Frame CyberDeck does not receive the
room mesh or copy it to another device.

## Agent command allowlist

The app can invoke only these agent commands:

| Group | Commands |
| --- | --- |
| Device | `info`, `battery` |
| Room | `room_status`, `room_capture`, `room_capture_status`, `room_capture_stop`, `room_put`, `room_delete`, `room_from_boundary`, `room_build`, `room_refine` |
| Settings | `set_settings` |
| Video codec | `video_codec_status`, `video_codec_switch` |

The vendored agent can do more, including Steam shortcut and Lepton operations,
but those stay outside this allowlist. Deployment, shortcut management, and
launching remain in Frame CyberDeck.

## Limitations

- **No per-game provisioning.** A captured room is not installed into MR titles
  by Frame CyberDeck. Titles that need a provisioned room must be handled
  outside this flow for now.
- **On-device capture only.** The app reports status; it does not preview or
  edit the captured mesh.
- **One Frame path validated.** Capture was verified on one Frame in the 0.2.0
  cycle. Other Frame builds and room shapes may behave differently.
- **Scan quality is environmental.** Lighting, reflective surfaces, and fast
  movement can cause a failed or incomplete scan. Retry after improving the
  room conditions.
- **Agent sync requires Devkit SSH.** If the Devkit connection is down, the scan
  cannot start.
- **No automatic retry.** A failed scan is reported; start a new scan after
  correcting the problem.

See [VERIFICATION.md](VERIFICATION.md) for the tested room path and
[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) for the release-wide limits.
