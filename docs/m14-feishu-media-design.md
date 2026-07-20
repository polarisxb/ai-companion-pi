# M14 Feishu Media Messages Design

Status: M14.1-M14.4 code and gates implemented; real-traffic stages await the Pi
Last updated: 2026-07-20

## Why M14

M13 gives the companion a text voice on Feishu. M14 gives her two richer
expressions on the same channel, both requiring zero extra hardware:

- **Voice bubbles**: reply text synthesized on the Pi (Piper TTS, local and
  offline, reusing the legacy voice investment) into opus and delivered as a
  playable Feishu voice message.
- **Image attachments**: she can attach her own creations
  (`creations/art/...`) to a chat reply through dialogue metadata.

M14 is outbound-media-only on the chat reply path. Understanding inbound
media (your photos, your voice) needs vision/ASR and is a later milestone.

## Confirmed Direction

- Media is an **enhancement, never a dependency**: the text reply is always
  sent first; any TTS, upload, or send failure downgrades silently to the
  already-delivered text and is recorded in the attempt ledger. A media
  failure can never fail a reply.
- **TTS backend is pluggable and command-driven**: a config command template
  (default documented for Piper: reads text on stdin, writes a wav) produces
  audio, then ffmpeg converts to opus 16k mono and ffprobe measures duration
  (both required by Feishu voice bubbles; ffmpeg is a system package on the
  Pi). `FakeTTSBackend` keeps every test and dry run hermetic.
- **Voice modes** (`voice_replies` in the feishu config):
  - `off` (default): text only.
  - `always`: every reply within `voice_max_chars` also goes out as voice.
  - `companion_choice`: she decides per reply via
    `===DIALOGUE_METADATA=== {"voice": true}`; the prompt hint for this is
    injected only when the mode is active, so M7/M8 dialogue behavior is
    byte-identical otherwise.
  - Replies longer than `voice_max_chars` (default 220) stay text-only.
- **Image attachments** (`image_attachments_enabled`, default false): the
  model may attach files via
  `{"attachments": [{"type": "image", "path": "creations/art/x.png"}]}`.
  Validation is strict: the resolved path must stay inside the companion
  `creations/` directory (no traversal, no symlink escape), the file must
  exist, carry an image extension (png/jpg/jpeg/gif/webp), fit
  `image_max_bytes` (default 10 MB), and at most `max_images_per_reply`
  (default 3) are sent.
- **Feishu upload plumbing** uses stdlib multipart (no new HTTP dependency):
  `im/v1/images` for images (image_key), `im/v1/files` with
  `file_type=opus` + duration for voice (file_key), then `msg_type=image` /
  `msg_type=audio` sends, with the same cached-token + stale-token-retry
  discipline as text.
- **Transport capability gating**: only transports with
  `supports_media = True` (Feishu real + fake) get media; the Signal
  transport silently skips media so shared bridge code stays safe.
- Ledger records gain a hashed `media` payload (voice sent/duration/error,
  image count/relative paths/errors). No audio bytes, no image bytes, no
  synthesized text beyond what the transcript already holds. Synthesized
  audio lives in a per-reply temp dir and is deleted after send.

## Runtime Shape

```text
bridge reply (M13 path, unchanged) -> text sent
  -> media step (only when transport.supports_media):
       voice decision (mode + metadata + length)
         -> CommandTTSBackend: template command -> wav -> ffmpeg -> opus
         -> ffprobe duration -> transport.send_voice(open_id, opus, ms)
       attachments from DIALOGUE_METADATA
         -> validate path/ext/size/count inside creations/
         -> transport.send_image(open_id, path)
  -> attempt record gains media outcome; failures recorded, reply unaffected
```

## Config additions (feishu chat config)

```json
{
  "voice_replies": "off",
  "voice_max_chars": 220,
  "tts_command": "piper --model /home/pi/piper-voices/zh_CN-huayan-medium.onnx --output_file {output}",
  "image_attachments_enabled": false,
  "max_images_per_reply": 3,
  "image_max_bytes": 10485760
}
```

`{output}` is replaced with the target wav path; text is fed on stdin. An
optional `{text}` placeholder is substituted as an argument for engines like
edge-tts that cannot read stdin.

## Boundaries

```json
{
  "text_reply_never_blocked_by_media": true,
  "attachments_outside_creations": false,
  "raw_media_bytes_in_ledger_or_reports": false,
  "synthesized_audio_retained": false,
  "inbound_media_understanding": false,
  "wake_or_scheduler_touched": false,
  "memory_authority_expanded": false
}
```

## Stages

### M14.1 Media engine + dry-run gate

```text
companion_core/tts.py
companion_core/chat_media.py
companion_core/feishu_transport.py (multipart upload + image/audio send)
companion_core/dialogue.py (DialogueResult.metadata + optional prompt hints)
companion_core/m14_feishu_media_dry_run.py
scripts/run_m14_feishu_media_dry_run.py
tests/test_m14_tts.py / test_m14_chat_media.py / test_m14_feishu_media.py / test_m14_gates.py
life-loop/m14_feishu_media_dry_run_report.json
```

Acceptance: fake TTS + fake transport cover voice always/companion-choice/
off/too-long/TTS-failure/upload-failure; image valid/missing/traversal/
oversize/over-count/disabled; multipart bodies verified against a stubbed
HTTP layer; text reply survives every media failure; ledger media payloads
are hashed-only. Recommendation: `m14_feishu_media_dry_run_ready`.

### M14.2 Supervised media trial (Pi)

One real voice bubble and one real image behind
`--confirm-real-feishu-send`, requiring M13.2 trial evidence and M14.1.
Writes `life-loop/m14_feishu_media_trial_report.json`
(`m14_feishu_media_trial_ready`).

### M14.3 Observation

Read-only ledger analysis of media outcomes on live/trial feishu records:
failure rates, size/count discipline, no raw bytes. Writes
`life-loop/m14_feishu_media_observation_report.json`
(`m14_feishu_media_observation_ready`).

### M14.4 Freeze

Read-only freeze requiring M14.1-M14.3 plus the M13.5 chat freeze. Writes
`life-loop/m14_feishu_media_freeze_report.json`
(`m14_feishu_media_frozen`).

## Explicit Non-goals

- No inbound media understanding (vision/ASR) — later milestones.
- No media in M11 outbox delivery yet (voice/image proactive messages are a
  follow-up once chat media is frozen).
- No video, stickers, cards, or files.
- No cloud TTS dependency; the default engine is local Piper.
- No new always-on service; media rides the M13 listener.

## Open Questions

- Which Piper Chinese voice model sounds right for her? Owner: user, decided
  on the Pi by listening. Impact: config value only.
- Should `companion_choice` voice hints eventually mention her current mood
  (voice when affectionate)? Revisit after observation.
