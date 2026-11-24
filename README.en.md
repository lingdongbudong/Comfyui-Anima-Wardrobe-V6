# Comfyui-Anima-Wardrobe-V6

<p align="right">
  <a href="README.md">中文</a> ｜ <b>English</b>
</p>

> [!CAUTION]
> **⛔ Commercial use prohibited · No repackaging or redistribution** — Copyright © 2026 灵冻不冻 (lingdongbudong). All rights reserved.
> This repository is licensed under the **[PolyForm Strict License 1.0.0](LICENSE)** as of **2026-11-24**.
>
> - **❌ No commercial use** — you may not use this plugin (source, prompt pools, scene / character / outfit libraries, presets, or any derivative) for any commercial purpose: paid distribution, bundling, paid generation services, commercial courses, or as the technical base of a commercial service. Written permission is required.
> - **❌ No repackaging or redistribution** — do not repackage, rename, wrap, re-upload, or include this plugin in third-party bundles, mirror archives, net-disk collections or resource sites, **modified or not**.
> - **❌ Do not remove or alter** this notice, the author attribution, or the source link.
> - **✅ Permitted** — personal non-commercial use (research, study, hobby, generating your own images) and modifying the source for your own use; quoting screenshots / recordings for non-commercial sharing with full attribution and this notice intact.
>
> **Commercial / derivative-work / repost licensing** → [Bilibili DM](https://space.bilibili.com/516891894) for written permission.
> **Legacy versions** published before **2026-11-24** remain under the license they were published with (GPL-3.0); that grant is not withdrawn.
> Full terms: **[NOTICE.md](NOTICE.md)**.

---

## What it is

A ComfyUI custom node that assembles a complete ANIMA3 prompt body by sampling across five
NSFW tiers — feed the output into the "global positive prompt" input of an Anima workflow.

Fully local · zero third-party dependencies · no network access at runtime ·
**generates prompts only, never images**.

> ⚠️ **Adult content.** Tiers 3–4 contain explicit material. Every character is treated as a
> fictional adult aged 18+. You are responsible for compliance with local law, the model's
> terms of use, and the policy of any platform you publish to.

## Nodes

Four nodes under the `AnimaWardrobeV6` category:

| Node | Purpose |
| --- | --- |
| Anima Prompt Random (V6) | main node — samples one full prompt body |
| Anima Prompt Level Grader (V6) | judges the tier only, never rewrites |
| Anima Prompt Merge (V6) | merges sampled output with your own text |
| Anima Prompt Check & Repair (V6) | runs the self-check on any prompt and repairs it |

The main node exposes four buttons (`⚙ open panel` · `🔁 reseed` · `🎲 reroll every queue` ·
`🧹 clear weights`); most configuration lives in the 8-tab panel rather than on the node.

## What's inside

| Item | Count |
| --- | --- |
| Lexicon | 15 sections / 57 fields / **1,139** options, each tagged with a tier |
| Characters | **2,432** |
| Scene presets | **782** |
| Outfit library | **167** sets across 11 categories |
| Official character outfits | **2,800** across 2,434 characters (297 with more than one) |
| Panel tabs | 8 |

## Install

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/lingdongbudong/Comfyui-Anima-Wardrobe-V6.git
```

No dependencies, no `requirements.txt`, no configuration. Restart ComfyUI.
(The folder name must stay `Comfyui-Anima-Wardrobe-V6`.)

Health check — no model needed:

```bash
curl --noproxy '*' http://127.0.0.1:8188/anima-wardrobe-v6/health
```

## Usage in one line

Drop **Anima Prompt Random (V6)** on the canvas, wire its `text` output into your
CR Text node's empty `text` input, start at `nsfw_level = sfw`, then open the panel to pick
characters, outfits, scenes or poses.

## Language

The complete documentation — every parameter, the tier matrix, the three-layer weight system,
the 35-item FAQ, the changelog and the maintainer notes — is written in Chinese:
**[README.md](README.md)**. This page covers the essentials only.

## License

**[PolyForm Strict License 1.0.0](LICENSE)** © 2026 灵冻不冻 (lingdongbudong).

- ❌ **No commercial use** without prior written permission.
- ❌ **No repackaging or redistribution**, modified or not.
- ✅ Personal non-commercial use, and modifying the source for your own use.
- Legacy versions published before 2026-11-24 remain under their original license (GPL-3.0);
  that grant is not withdrawn.

Commercial / derivative licensing: contact the author via [Bilibili DM](https://space.bilibili.com/516891894).
Full terms: [NOTICE.md](NOTICE.md) · Chinese documentation: [README.md](README.md).
