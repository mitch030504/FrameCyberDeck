# Third-party notices

Frame CyberDeck is an independent GPL-3.0-only fork of **VR CyberDeck** by
DeliciousMeatPop, which was built on **ApprenticeVR** by jimzrt. This fork is
not an official VR CyberDeck release and is not endorsed by its maintainer.

The following projects are included, adapted, or invoked. Their own licenses
and notices apply to their material.

## FramePort

- Project: https://github.com/spoopyghosty0/frameport
- License: GPL-3.0-only
- Pinned revision: `72c71fbbd303886992ab62cbc1aea6287100f935`
- Material: the vendored headless build engine, game recipes, FrameBridge
  adapter, shim and scanner artifacts, and the on-Frame agent.
- The full upstream license and third-party notice file are included under
  `vendor/frameport/` in the source tree and in the installed resources.
- Local modifications: the Flet UI is not included; the ELF reader treats
  stripped/malformed section tables as empty; a JSON-lines runner integrates
  the engine. See `vendor/frameport/PIN.json` and
  `vendor/frameport/MANIFEST.sha256`.

## OVRPort / Android-XR-Bridge

- Project: https://github.com/Android-XR-Bridge/OVRPort
- License: GPL-3.0
- Material: the OVRPort CLI and runtime libraries used during conversion.

## AXRB and RiftLift

- AXRB: https://github.com/Android-XR-Bridge/AXRB — GPL-3.0
- RiftLift: GPL-3.0-or-later
- Material: the Meta native SSO protocol and resumable, size-verified download
  approach adapted for the Meta owned-title sync.

## Venture

- Project: https://github.com/Villagers654/Venture
- License: GPL-3.0-or-later
- Venture is an external, optional program. Frame CyberDeck does not bundle it;
  it invokes a locally installed `venture-cli` for the PC target.

## Trademarks and affiliation

Valve, Steam, SteamOS, Steam Frame, SteamVR and Lepton are trademarks of Valve
Corporation. Meta, Meta Quest and Horizon are trademarks of Meta Platforms,
Inc. FramePort is a project by spoopyghosty0. Venture is a project by
Villagers654.

Frame CyberDeck is not affiliated with, endorsed by, or sponsored by Valve,
Meta, FramePort, Venture, or the VR CyberDeck maintainer. Quest titles remain
subject to Meta's terms; use only content you own.
