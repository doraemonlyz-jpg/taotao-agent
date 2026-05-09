---
name: video-moderation
description: Pipeline for analysing a video for policy violations
when_to_use: the user uploads a video file or video URL for review/moderation
---

# Recipe — video-moderation

A video moderation request has four mandatory phases. Run them in order.
If any phase yields strong evidence of a violation, you may short-circuit
to the verdict — but you MUST still record what you skipped.

### 1. Decode & sample
- Use `python_repl` + `ffmpeg` to extract:
  - 1 keyframe / second (image jpegs)
  - mono 16 kHz audio (wav)
  - container metadata (duration, codec, dimensions)

### 2. Multimodal pass
- For visuals: feed each keyframe to a VLM (Qwen2.5-VL / GPT-4V) with the
  policy schema as a function. Aggregate by category.
- For audio: ASR with Whisper-large; language-detect; toxic-speech score.
- For overlays/captions: OCR pass on each keyframe.

### 3. Cross-modal reasoning
- Build a single timeline `[t, visual_tags, asr_text, ocr_text]`.
- Reason holistically — do NOT decide based on one modality alone.
  (e.g. a violent scene + neutral commentary may be educational news.)

### 4. Verdict
- Output: `{decision: allow|review|remove, categories: [...], confidence,
  evidence: [{t, why}]}`.
- If `confidence < 0.7` → recommend `review`, never auto-remove.
- Always cite at least one timestamp per category triggered.

Tools to prefer: `python_repl` (ffmpeg, audio loaders), `web_search` (only
for policy clarification), `remember` (only to persist policy updates the
user told you about — never to persist user-uploaded content).
